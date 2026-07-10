import importPlugin from "eslint-plugin-import";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// bulletproof-react boundary enforcement (the only rule we run here — this
// config is intentionally not a full style linter). Two layers:
//   1. unidirectional: shared -> features -> app (no upward imports)
//   2. no cross-feature imports (compose features at the app layer)
// The integrated data layer (stores, actions, api) is SHARED — features may
// import it freely; it may not import features/app.

const FEATURES = [
  "chat",
  "sessions",
  "automations",
  "memory",
  "settings",
  "command-palette",
  "background-agents",
  "home",
  "slices",
  "sources",
];

const SHARED = [
  "./src/lib",
  "./src/components/ui",
  "./src/stores",
  "./src/hooks",
  "./src/api",
  "./src/actions",
];

const sharedZones = SHARED.map((target) => ({
  target,
  from: ["./src/features", "./src/app"],
  message:
    "Unidirectional violation: shared code must not import from features/ or app/ (shared -> features -> app).",
}));

const crossFeatureZones = FEATURES.map((f) => ({
  target: `./src/features/${f}`,
  from: "./src/features",
  except: [`./${f}`],
  message: `Cross-feature import: features/${f} may not import another feature. Lift shared UI to src/components/ui, or shared logic to src/lib | src/actions | src/api | src/stores.`,
}));

// Atomicity consistency: a raw <input>/<textarea> carrying the `.input-field`
// material is an ad-hoc re-derivation of the Input/Textarea/Field primitives.
// Enforced in features/app only — the primitives in components/ui own .input-field.
const noRawInputField = [
  {
    selector:
      "JSXOpeningElement[name.name=/^(input|textarea)$/] JSXAttribute[name.name='className'] Literal[value=/input-field/]",
    message:
      "Ad-hoc input chrome: use <Input>/<Textarea>/<Field> from @/components/ui instead of a raw <input>/<textarea> with the .input-field class.",
  },
  {
    selector:
      "JSXOpeningElement[name.name=/^(input|textarea)$/] JSXAttribute[name.name='className'] TemplateElement[value.raw=/input-field/]",
    message:
      "Ad-hoc input chrome: use <Input>/<Textarea>/<Field> from @/components/ui instead of a raw <input>/<textarea> with the .input-field class.",
  },
];

export default tseslint.config(
  {
    files: ["src/**/*.{ts,tsx}"],
    // This config only enforces import boundaries; it doesn't run react-hooks
    // rules, so don't flag the codebase's `eslint-disable react-hooks/*`
    // directives as unused.
    linterOptions: { reportUnusedDisableDirectives: "off" },
    // react-hooks registered only so existing `// eslint-disable react-hooks/*`
    // comments resolve; its rules are intentionally NOT enabled here.
    plugins: { import: importPlugin, "react-hooks": reactHooks },
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: {
      "import/resolver": { typescript: { project: "./tsconfig.json" } },
    },
    rules: {
      "import/no-restricted-paths": [
        "error",
        {
          zones: [
            ...sharedZones,
            { target: "./src/features", from: "./src/app" },
            ...crossFeatureZones,
          ],
        },
      ],
    },
  },
  {
    // Atomicity: ban ad-hoc input chrome outside the primitives.
    files: ["src/features/**/*.{ts,tsx}", "src/app/**/*.{ts,tsx}"],
    rules: { "no-restricted-syntax": ["error", ...noRawInputField] },
  },
);
