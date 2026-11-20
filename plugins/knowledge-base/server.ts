import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import type { BbPluginApi } from "@get-bb/plugin-sdk";

/** The knowledge base is a git checkout on the BB server host, where this plugin runs. */
export const instructions = (root: string) => `Личная база знаний пользователя (git-репозиторий ${root} на сервере BB) доступна из любого треда инструментами kb_search, kb_read, kb_topics, kb_add — они работают с любой машины, копировать базу не нужно.
Обращайся к ней: когда спрашивают о ранее сохранённых материалах («у нас было», «в базе», «что сохраняли про…», «тот рилс/репо/гайд»); когда просят «запиши/сохрани/добавь в базу»; при разборе рилса, ссылки, репозитория или гайда для базы; когда полезно проверить, есть ли уже материал по теме. Сначала kb_search (слова, тег, тема), затем kb_read нужного айтема. Перед kb_add всегда ищи дубль и вызови kb_topics: он вернёт темы и правила базы, включая штатные инструменты скачивания и распознавания медиа — свои не ставь. kb_add сам проверит источник/slug, присвоит id, пересоберёт индексы и сделает локальный коммит (без push). Индексы руками не правь. Подробности — навык knowledge-base.`;

/** Default base: a folder in the home directory of the user that runs this BB server. Created empty on first use. */
export const DEFAULT_ROOT = "~/knowledge-base";

export function expandRoot(value: string) {
  const root = value.trim() || DEFAULT_ROOT;
  return root === "~" ? homedir() : root.startsWith("~/") ? resolve(homedir(), root.slice(2)) : resolve(root);
}

let configuredRoot = expandRoot(process.env.KB_ROOT || "");

function scriptPath() {
  const directory = dirname(fileURLToPath(import.meta.url));
  const path = [resolve(directory, "kb.py"), resolve(directory, "../kb.py")].find(existsSync);
  if (!path) throw new Error("kb.py not found next to the plugin");
  return path;
}

export function kbRoot() {
  return configuredRoot;
}

export async function runKb(args: string[], stdin?: string, signal?: AbortSignal, before?: () => Promise<void>): Promise<{ code: number; json: unknown; raw: string }> {
  if (before) await before();
  return new Promise((resolveRun, reject) => {
    const child = execFile("python3", [scriptPath(), "--root", kbRoot(), ...args],
      { timeout: 120_000, maxBuffer: 8 * 1024 * 1024, signal, env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (error, stdout, stderr) => {
        const code = error ? (typeof (error as { code?: unknown }).code === "number" ? (error as { code: number }).code : 1) : 0;
        try {
          resolveRun({ code, json: JSON.parse(stdout), raw: stdout });
        } catch {
          if (error) reject(new Error(`kb.py failed: ${stderr || error.message}`));
          else resolveRun({ code, json: null, raw: stdout });
        }
      });
    if (stdin !== undefined) child.stdin?.end(stdin);
    else child.stdin?.end();
  });
}

const MAX_TOOL_TEXT = 60_000;
function asText(value: unknown) {
  const text = JSON.stringify(value, null, 1);
  return text.length > MAX_TOOL_TEXT ? text.slice(0, MAX_TOOL_TEXT) + "\n…(truncated)" : text;
}

export const addSchema = z.object({
  topic: z.string().min(2).max(60).describe("Existing topic folder, see kb_topics."),
  title: z.string().min(2).max(200).describe("Human title (becomes the # heading)"),
  slug: z.string().min(2).max(80).describe("Short kebab-case latin slug, unique across the KB"),
  source: z.string().min(1).max(2000).describe("Source URL or short source description"),
  type: z.string().min(2).max(60).describe("github-repo | article | ig-reel | person | product | how-to-guide | guide-pdf | …"),
  tags: z.array(z.string().min(1).max(60)).min(1).max(30),
  body: z.string().min(1).max(200_000).describe("Markdown body WITHOUT frontmatter and title: sections ## Что внутри / Что делает, ## Зачем нам, ## Связанные айтемы ([[slug]]), ## Источник в чате"),
  status: z.enum(["pointer", "researched", "in-progress", "analyzed"]).optional(),
  createTopic: z.boolean().optional().describe("Only when no existing topic fits"),
  force: z.boolean().optional().describe("Add even if a duplicate source was found (use a distinct slug)"),
  dryRun: z.boolean().optional(),
});

export function addArgs(input: z.output<typeof addSchema>) {
  const args = ["add", "--topic", input.topic, "--title", input.title, "--slug", input.slug, "--source", input.source,
    "--type", input.type, "--tags", input.tags.join(",")];
  if (input.status) args.push("--status", input.status);
  if (input.createTopic) args.push("--create-topic");
  if (input.force) args.push("--force");
  if (input.dryRun) args.push("--dry-run");
  return args;
}

export default function knowledgeBase(bb: BbPluginApi) {
  const settings = bb.settings.define({
    root: { type: "string", label: "Knowledge base folder on the BB server (a git checkout; an empty base is created there on first use)", default: DEFAULT_ROOT },
  });
  const refresh = async () => {
    try { configuredRoot = expandRoot(process.env.KB_ROOT || String((await settings.get()).root || "")); }
    catch (error) { bb.log.warn(`knowledge-base settings: ${String(error)}`); }
  };
  void refresh();
  // Serialize writers inside this process; kb.py also takes a file lock.
  let writes: Promise<unknown> = Promise.resolve();

  bb.agents.registerTool({
    name: "kb_search",
    description: "Search the user's knowledge base (saved reels, repos, guides, products, notes) by words, tag and/or topic. Empty query lists the newest items. Returns path, title, tags, source and a snippet.",
    parameters: z.object({
      query: z.string().max(500).optional().describe("Words to find (Russian or English); all words must match, otherwise any"),
      topic: z.string().max(60).optional(),
      tag: z.string().max(60).optional(),
      limit: z.number().int().min(1).max(50).optional(),
    }),
    async execute(input, ctx) {
      const args = ["search", "--limit", String(input.limit ?? 10)];
      if (input.topic) args.push("--topic", input.topic);
      if (input.tag) args.push("--tag", input.tag);
      if (input.query) args.push("--", ...input.query.split(/\s+/).filter(Boolean));
      return asText((await runKb(args, undefined, ctx.signal, refresh)).json);
    },
  });

  bb.agents.registerTool({
    name: "kb_read",
    description: "Read one knowledge base item in full. ref = path from kb_search, slug, NNN-slug or topic/NNN.",
    parameters: z.object({ ref: z.string().min(1).max(500) }),
    async execute(input, ctx) {
      return asText((await runKb(["read", input.ref], undefined, ctx.signal, refresh)).json);
    },
  });

  bb.agents.registerTool({
    name: "kb_topics",
    description: "List knowledge base topics with item counts.",
    parameters: z.object({}),
    async execute(_input, ctx) {
      return asText((await runKb(["topics"], undefined, ctx.signal, refresh)).json);
    },
  });

  bb.agents.registerTool({
    name: "kb_add",
    description: "Add a new item to the knowledge base using the standard template: checks duplicates (source URL / slug), assigns the next id in the topic, regenerates indexes and makes a local git commit (no push). Search first.",
    parameters: addSchema,
    async execute(input, ctx) {
      const work = writes.catch(() => {}).then(() => runKb(addArgs(input), input.body, ctx.signal, refresh));
      writes = work;
      return asText((await work).json);
    },
  });

  bb.agents.configure(() => ({ tools: ["kb_search", "kb_read", "kb_topics", "kb_add"], skills: ["knowledge-base"], instructions: instructions(configuredRoot) }));

  bb.cli.register({
    name: "kb",
    summary: "Search, read and add items in the user's knowledge base (runs on the BB server)",
    commands: [
      { name: "search", summary: "Search items", usage: "search [--topic T] [--tag X] [--limit N] [words…]" },
      { name: "read", summary: "Print one item", usage: "read <path|slug|NNN-slug|topic/NNN>" },
      { name: "topics", summary: "List topics", usage: "topics" },
      { name: "add", summary: "Add item; body from --body-text", usage: "add --topic T --title … --slug … --source … --type … --tags a,b [--status S] --body-text '…' [--force] [--create-topic] [--dry-run]" },
      { name: "remove", summary: "Remove an item (cleanup)", usage: "remove <ref>" },
    ],
    async run(argv, ctx) {
      const [command, ...rest] = argv;
      if (!command || !["search", "read", "topics", "add", "remove"].includes(command)) {
        return { exitCode: 2, stderr: "Usage: bb kb search|read|topics|add|remove … (see bb kb --help)\n" };
      }
      let body: string | undefined;
      const args = [...rest];
      const index = args.indexOf("--body-text");
      if (index >= 0) {
        body = args[index + 1] ?? "";
        args.splice(index, 2);
      }
      if (command === "add" && body === undefined) return { exitCode: 2, stderr: "add requires --body-text '<markdown body>'\n" };
      const run = () => runKb([command, ...args], body, ctx.signal, refresh);
      const result = command === "add" || command === "remove"
        ? await (writes = writes.catch(() => {}).then(run)) as Awaited<ReturnType<typeof run>>
        : await run();
      const stdout = result.raw.endsWith("\n") ? result.raw : result.raw + "\n";
      return { exitCode: result.code, stdout };
    },
  });
}
