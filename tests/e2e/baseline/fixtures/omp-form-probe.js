export default function (pi) {
  pi.on("before_agent_start", async (_event, ctx) => {
    const answer = await ctx.ui.select("Approve the E2E form probe?", ["Approve", "Deny"]);
    if (answer === "Approve") {
      throw new Error("The E2E form probe must not be approved");
    }
  });
}
