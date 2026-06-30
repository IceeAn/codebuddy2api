/** @type {import('prettier').Config & import('prettier-plugin-tailwindcss').PluginOptions} */
export default {
  printWidth: 100,
  semi: true,
  singleQuote: true,
  trailingComma: 'all',
  htmlWhitespaceSensitivity: 'css',
  endOfLine: 'lf',
  tailwindStylesheet: './src/styles.css',
  plugins: ['prettier-plugin-tailwindcss'],
};
