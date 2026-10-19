const fs = require('fs');

// 1. Buat Ikon Sederhana (SVG)
fs.mkdirSync('resources', { recursive: true });
fs.writeFileSync('resources/orion-icon.svg', '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"><path d="M12 2L2 22h20L12 2zm0 3.5L18.5 20H5.5L12 5.5z"/></svg>');

// 2. Update package.json
const pkg = {
    name: "orion",
    displayName: "Orion",
    description: "AI Software Engineer Assistant",
    version: "0.0.1",
    engines: { vscode: "^1.125.0" },
    categories: ["Other"],
    activationEvents: ["*"],
    main: "./dist/extension.js",
    contributes: {
        viewsContainers: {
            activitybar: [
                { id: "orion-activitybar", title: "Orion", icon: "resources/orion-icon.svg" }
            ]
        },
        views: {
            "orion-activitybar": [
                { id: "orionActivityView", name: "Orion Chat" }
            ]
        },
        commands: [
            { command: "orion.helloWorld", title: "Hello World" },
            { command: "orion.testBackend", title: "Orion: Test Backend Connection" },
            { command: "orion.openChat", title: "Orion: Open Chat Panel" }
        ]
    },
    scripts: {
        "vscode:prepublish": "npm run package",
        compile: "npm run check-types && npm run lint && node esbuild.js",
        watch: "npm-run-all -p watch:*",
        "watch:esbuild": "node esbuild.js --watch",
        "watch:tsc": "tsc --noEmit --watch --project tsconfig.json",
        package: "npm run check-types && npm run lint && node esbuild.js --production",
        "compile-tests": "tsc -p . --outDir out",
        "watch-tests": "tsc -p . -w --outDir out",
        pretest: "npm run compile-tests && npm run compile && npm run lint",
        "check-types": "tsc --noEmit",
        lint: "eslint src",
        test: "vscode-test"
    },
    devDependencies: {
        "@types/vscode": "^1.125.0",
        "@types/mocha": "^10.0.10",
        "@types/node": "24.x",
        "typescript-eslint": "^8.61.1",
        eslint: "^10.5.0",
        esbuild: "^0.28.1",
        "npm-run-all": "^4.1.5",
        typescript: "^6.0.3",
        "@vscode/test-cli": "^0.0.15",
        "@vscode/test-electron": "^3.0.0"
    },
    overrides: { diff: "^8.0.4", "serialize-javascript": "^7.0.6" }
};
fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2), 'utf8');
console.log('SUCCESS: Activity Bar & Icon created');