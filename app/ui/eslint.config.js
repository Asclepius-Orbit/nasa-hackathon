import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const bannedProps = ['innerHTML', 'outerHTML', 'insertAdjacentHTML'].map((property) => ({
  property,
  message: `${property} is banned (XSS); render through React.`,
}));

export default tseslint.config(
  {
    ignores: ['out/**', 'release/**', 'dist*/**', 'coverage/**', 'src/contracts/v1.ts', 'node_modules/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  { rules: { '@typescript-eslint/no-unused-vars': ['error', { ignoreRestSiblings: true }] } },
  {
    files: ['**/*.{ts,tsx}'],
    plugins: { react, 'react-hooks': reactHooks, 'jsx-a11y': jsxA11y },
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    settings: { react: { version: 'detect' } },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.strict.rules,
      'react/no-danger': 'error',
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-restricted-properties': [
        'error',
        ...bannedProps,
        { object: 'document', property: 'write', message: 'document.write is banned.' },
        { object: 'document', property: 'writeln', message: 'document.writeln is banned.' },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: 'AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]',
          message: 'innerHTML/outerHTML assignment is banned.',
        },
        {
          selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
          message: 'insertAdjacentHTML is banned.',
        },
      ],
    },
  },
  {
    files: ['**/*.{js,cjs,mjs}'],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: { ...tseslint.configs.disableTypeChecked.languageOptions, globals: globals.node },
    rules: { ...tseslint.configs.disableTypeChecked.rules, '@typescript-eslint/no-require-imports': 'off' },
  },
);
