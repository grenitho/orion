const esbuild = require("esbuild");

const production = process.argv.includes('--production');
const watch = process.argv.includes('--watch');

/**
 * @type {import('esbuild').Plugin}
 */
const esbuildProblemMatcherPlugin = {
	name: 'esbuild-problem-matcher',

	setup(build) {
		build.onStart(() => {
			console.log('[watch] build started');
		});
		build.onEnd((result) => {
			result.errors.forEach(({ text, location }) => {
				console.error(`✘ [ERROR] ${text}`);
				console.error(`    ${location.file}:${location.line}:${location.column}:`);
			});
			console.log('[watch] build finished');
		});
	},
};

async function main() {
	const ctx = await esbuild.context({
		entryPoints: [
			'src/extension.ts'
		],
		bundle: true,
		format: 'cjs',
		minify: production,
		sourcemap: !production,
		sourcesContent: false,
		platform: 'node',
		outfile: 'dist/extension.js',
		external: ['vscode'],
		logLevel: 'silent',
		plugins: [
			/* add to the end of plugins array */
			esbuildProblemMatcherPlugin,
		],
	});
	if (watch) {
		await ctx.watch();
	} else {
		await ctx.rebuild();
		await ctx.dispose();
	}
	// >>> UPDATE-020 adendum-4: the chat webview runtime is a SEPARATE
	// browser-platform bundle (loaded by the webview via asWebviewUri).
	await esbuild.build({
		entryPoints: ['src/panels/chatWebview.ts'],
		bundle: false,
		format: 'iife',
		minify: production,
		sourcemap: !production,
		platform: 'browser',
		outfile: 'dist/chatwebview.js',
		logLevel: 'silent',
	});
}

main().catch(e => {
	console.error(e);
	process.exit(1);
});
