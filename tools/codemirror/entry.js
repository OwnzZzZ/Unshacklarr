// The editor Unshacklarr uses for unshackle.yaml: CodeMirror 6 (MIT), bundled once into one file.
export { basicSetup } from "codemirror";
export { EditorView, keymap } from "@codemirror/view";
export { EditorState, Compartment } from "@codemirror/state";
export { yaml } from "@codemirror/lang-yaml";
export { autocompletion, completionKeymap } from "@codemirror/autocomplete";
export { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
export { indentWithTab } from "@codemirror/commands";
export { tags } from "@lezer/highlight";
