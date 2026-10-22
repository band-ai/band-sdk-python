// Vendored verbatim from Eric's Adam One deck contract-contribution-outcomes-20261013
// (all four parts carry the same function; sha256 of the function text: 05c6539046efe1f75e0d7dcaeedfa6b535087c872ae88c28a94acbbb8ad85a06).
// Do not edit: spec/tools/test_band_highlight.py runs it with node to prove that
// bandhighlight.py in deck mode reproduces it byte for byte.
const esc=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Presentation-only token coloring. Escaping and text preservation are required.
function highlightContract(source) {
  const text=String(source), output=[];
  const emit=(value,kind)=>output.push(kind?`<span class="tok-${kind}">${esc(value)}</span>`:esc(value));
  const declarations=new Set(['contract','role','type','state','end','anystate','room','watch','require','event','due','on','collect','reply','failure','offline']);
  let i=0;
  while(i<text.length) {
    const rest=text.slice(i), start=i;
    if(rest.startsWith('%%')) {
      const end=text.indexOf('\n',i);i=end<0?text.length:end;emit(text.slice(start,i),'comment');continue;
    }
    if(rest.startsWith('/*')) {const end=text.indexOf('*/',i+2);i=end<0?text.length:end+2;emit(text.slice(start,i),'comment');continue;}
    const triple=/^~[Ss]"""/.test(rest)?5:rest.startsWith('"""')?3:0;
    if(triple) {
      const end=text.indexOf('"""',i+triple);i=end<0?text.length:end+3;emit(text.slice(start,i),'string');continue;
    }
    if(/^~[SRWsrw]/.test(rest) && '/|{[(<"\''.includes(rest[2]||'\0')) {
      const open=rest[2],close=({'{':'}','[':']','(':')','<':'>'})[open]||open;
      i+=3;
      while(i<text.length && text[i]!==close) {
        if(text[i]==='\\' && i+1<text.length)i+=2;else i++;
      }
      if(i<text.length)i++;
      emit(text.slice(start,i),'string');continue;
    }
    if(rest[0]==='"' || rest[0]==="'") {
      const quote=rest[0];i++;
      while(i<text.length && text[i]!==quote) {if(text[i]==='\\')i+=2;else i++;}
      if(i<text.length)i++;
      i=Math.min(i,text.length);emit(text.slice(start,i),'string');continue;
    }
    let scope=rest.match(/^@(self|state|contract)\b/);
    if(scope){i+=scope[0].length;emit(scope[0],'reference');continue;}
    let token=rest.match(/^\$[a-z][a-z0-9_]*/);
    if(token) {i+=token[0].length;emit(token[0],'reference');continue;}
    token=rest.match(/^@?[0-9]+(?:[smhd]\b)?/);
    if(token) {i+=token[0].length;emit(token[0],/[@smhd]/.test(token[0])?'duration':'number');continue;}
    token=rest.match(/^[a-zA-Z_][a-zA-Z0-9_-]*/);
    if(token) {
      const value=token[0],word=value.toLowerCase();
      const lineStart=text.lastIndexOf('\n',i-1)+1;
      const before=text.slice(lineStart,i),after=text.slice(i+value.length);
      const lineEnd=text.indexOf('\n',i),line=text.slice(lineStart,lineEnd<0?text.length:lineEnd);
      let kind=null;
      if(declarations.has(word) && /^\s*$/.test(before) && !/^ -> /.test(after))kind='keyword';
      else if(['as','each','activity'].includes(word) && before.includes('->') && /\)/.test(before) && !before.slice(before.lastIndexOf(')')).includes('=>'))kind='keyword';
      else if(word==='max_entries' && /^\s*(?:state|\+STATE) /.test(line) && /^ [0-9]+ => /.test(after))kind='keyword';
      else if(['all','any','of','with','in'].includes(word) && /^\s*(?:due|DUE) (?!->)/.test(line))kind='keyword';
      else if(['online','offline','removed','for'].includes(word) && /^\s*(?:on|ON) (?!->)/.test(line))kind='keyword';
      else if(word==='from' && /^\s*collect /.test(line))kind='keyword';
      else if(word==='max' && /^\s*contract /.test(line))kind='keyword';
      else if(word==='infinity' && /^\s*contract /.test(line))kind='duration';
      else if(['text','ref','enum','integer','list'].includes(word) && after.startsWith('('))kind='type';
      else if(word==='boolean' && /:\s*$/.test(before))kind='type';
      else if(/^\s*:/.test(after) && !line.includes('->'))kind='field';
      i+=value.length;emit(value,kind);continue;
    }
    token=rest.match(/^(?:->|=>|\.\.|[|=:])/);
    if(token) {i+=token[0].length;emit(token[0],'operator');continue;}
    emit(text[i]);i++;
  }
  return output.join('');
}
if (typeof module !== 'undefined') module.exports = { highlightContract, esc };
