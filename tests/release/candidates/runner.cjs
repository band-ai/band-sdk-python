// Execute the pinned release library; only the GitHub file boundary is doubled.
require('release-please');
console.log = console.info = console.debug = (...args) => console.error(...args);
const fs = require('node:fs');
const path = require('node:path');
const {Python} = require('release-please/build/src/strategies/python');
const {LinkedVersions} = require('release-please/build/src/plugins/linked-versions');
const {Merge} = require('release-please/build/src/plugins/merge');
const {TagName} = require('release-please/build/src/util/tag-name');
const {parseConventionalCommits} = require('release-please/build/src/commit');
const root = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const files = input.files;
const github = {
  repository: {owner:'band-ai', repo:'band-sdk-python'},
  async getFileContentsOnBranch(name) {
    if (!(name in files)) throw Object.assign(new Error(name), {status:404, name:'FileNotFoundError'});
    return {parsedContent: files[name]};
  },
  async findFilesByFilenameAndRef() { return []; },
};
const config = JSON.parse(fs.readFileSync(path.join(root,'release-please-config.json')));
const settings = Object.fromEntries(Object.entries(config.packages).map(([p,c]) => [p, {
  releaseType:'python', packageName:c['package-name'], changelogPath:c['changelog-path'],
  extraFiles:(c['extra-files'] || []),
}]));
async function generate() {
  const strategies = Object.fromEntries(Object.entries(settings).map(([p,c]) => [p,new Python({...c,path:p,github,targetBranch:'main'})]));
  const commits = Object.fromEntries(Object.keys(settings).map(p => [p,(input.commits[p] || []).map((message,i)=>({sha:String(i).padStart(40,'0'),message,files:[]}))]));
  const releases = Object.fromEntries(Object.entries(input.versions).map(([p,v])=>[p,{tag:TagName.parse(`${settings[p].packageName}-v${v}`),sha:'a'.repeat(40),notes:''}]));
  const c=config.plugins[0];
  const linked=new LinkedVersions(github,'main',settings,c.groupName,c.components,{merge:c.merge});
  const prepared=await linked.preconfigure(strategies, commits, releases);
  const candidates=[];
  for(const [p,strategy] of Object.entries(prepared)) {
    const pr=await strategy.buildReleasePullRequest(parseConventionalCommits(commits[p]),releases[p]);
    if(pr) candidates.push({path:p,config:settings[p],pullRequest:pr});
  }
  const merged=await new Merge(github,'main',settings).run(await linked.run(candidates));
  return merged.map(c=>({branch:c.pullRequest.headRefName, notes:c.pullRequest.body.toString(), files:Object.fromEntries(c.pullRequest.updates.filter(u=>u.createIfMissing || u.path in files).map(u=>[u.path,u.updater.updateContent(files[u.path] || '')]))}));
}
generate().then(value=>process.stdout.write(JSON.stringify(value))).catch(error=>{console.error(error);process.exitCode=1;});
