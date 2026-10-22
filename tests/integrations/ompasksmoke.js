import { writeFile } from "node:fs/promises";

export default function (pi) {
  pi.registerCommand("band-ask-wire", {
    description: "Exercise structured ask without invoking a model",
    handler: async (_args, ctx) => {
      if (typeof ctx.ui.askDialog !== "function") {
        throw new Error("The native host did not enable the structured ask dialog");
      }
      const answer = await ctx.ui.askDialog([
        {
          id: "storage",
          question: "Database?",
          options: [{ label: "SQLite", description: "Embedded" }, { label: "Postgres" }],
          recommended: 0,
        },
        {
          id: "targets",
          question: "Deploy where?",
          options: [{ label: "Dev" }, { label: "Prod" }],
          multi: true,
        },
      ]);
      await writeFile(process.env.BAND_OMP_ASK_SMOKE_RECEIPT, JSON.stringify(answer));
    },
  });
}
