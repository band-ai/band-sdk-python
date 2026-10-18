import { appendFileSync } from "node:fs";

export default function catalogExtension(pi) {
  pi.registerProvider("band-catalog-fixture", {
    baseUrl: "http://127.0.0.1:9/v1",
    api: "openai-completions",
    apiKey: "catalog-metadata-only-fixture-key",
    models: [
      {
        id: "catalog-model",
        name: "Configured catalog fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 8192,
        maxTokens: 1024,
      },
    ],
  });
  appendFileSync(
    process.env.BAND_CATALOG_EXTENSION_RECEIPT,
    JSON.stringify({ models_command: process.argv.includes("models") }) + "\n",
  );
}
