import * as vscode from 'vscode';
import { BackendService } from './services/backendService';
import { ChatPanel } from './panels/ChatPanel';
import { PerformancePanel } from './panels/PerformancePanel';
import { ActivityProvider } from './panels/ActivityProvider';
import { SetupWizardPanel } from './panels/SetupWizardPanel';
import { McpPanel } from './panels/McpPanel';
import { ensureBackendRunning, initSetupGuide, getSetupGuideUrl, startStaleWatchdog, restartBackend, detectEnvironment } from './services/backendManager';


const backend = new BackendService();

export async function activate(context: vscode.ExtensionContext) {
	console.log('Orion Extension Active!');

	// >>> UPDATE-010: derive the setup-guide URL from this extension's
	// package.json repository metadata before anything can link to it.
	initSetupGuide(context);

	// >>> UPDATE-012: register ALL commands BEFORE awaiting the backend.
	// ensureBackendRunning() can pop a setup-error dialog whose
	// "Run Setup Wizard" button executes orion.openSetupWizard — if the
	// command were registered only after the await, that button would
	// fail with "command not found" on a fresh machine (exactly when the
	// wizard matters most).
	const activityProvider = new ActivityProvider();
	vscode.window.registerTreeDataProvider('orionActivityView', activityProvider);

	const openChat = vscode.commands.registerCommand('orion.openChat', () => {
		ChatPanel.createOrShow(activityProvider);
	});
	context.subscriptions.push(openChat);

	const showPerf = vscode.commands.registerCommand('orion.showPerformanceDashboard', () => {
		PerformancePanel.createOrShow();
	});
	context.subscriptions.push(showPerf);

	// >>> UPDATE-010: manual controls for the onboarding walkthrough and for
	// recovering when the auto-start failed (error dialogs link to these too).
	const startBackend = vscode.commands.registerCommand('orion.startBackend', () => ensureBackendRunning());
	// >>> UPDATE-025 commit 2: palette-level explicit restart. The owner
	// ritual (kill port 8000 by hand, then Start Backend) existed because
	// the backend outlives VS Code by design and deactivate() is empty;
	// .env edits and patched code stay invisible until a REAL restart.
	// One command now does kill-8000 + auto-start with a progress toast,
	// so "restart" never again depends on remembering two manual steps.
	const restartBackendCmd = vscode.commands.registerCommand('orion.restartBackend', async () => {
		await vscode.window.withProgress(
			{
				location: vscode.ProgressLocation.Notification,
				title: 'Orion: restarting backend (kill port 8000, then auto-start)…',
			},
			async () => {
				await restartBackend();
			}
		);
		const env = await detectEnvironment();
		if (env.backendRunning) {
			const ver = env.backendHealth?.version ?? 'unknown';
			void vscode.window.showInformationMessage(
				`Orion: backend restarted — version ${ver} on port 8000.`
			);
		} else {
			void vscode.window.showWarningMessage(
				'Orion: restart finished but port 8000 did not answer yet — give it a few seconds, then run "Orion: Start Backend" if it stays silent.'
			);
		}
	});
	// >>> UPDATE-020: a backend that goes stale MID-SESSION (git pull,
	// extension update) now toasts once per backend boot instead of waiting
	// for the next VS Code restart to be noticed.
	startStaleWatchdog();
	context.subscriptions.push(startBackend);
	context.subscriptions.push(restartBackendCmd);

	const openSetupGuide = vscode.commands.registerCommand('orion.openSetupGuide', async () => {
		const url = getSetupGuideUrl();
		if (url) {
			await vscode.env.openExternal(vscode.Uri.parse(url));
		} else {
			vscode.window.showInformationMessage('Orion: the setup guide lives at docs/PANDUAN_SETUP.md in the Orion repository.');
		}
	});
	context.subscriptions.push(openSetupGuide);

	// >>> UPDATE-012: "Route 1" — install the backend from inside VS Code.
	const openSetupWizard = vscode.commands.registerCommand('orion.openSetupWizard', () => {
		SetupWizardPanel.createOrShow(context.extensionUri);
	});
	context.subscriptions.push(openSetupWizard);

	// >>> UPDATE-015: configuration surface for MCP tool servers (the
	// foundation shipped in UPDATE-014). Registered before the backend
	// await like every other command, so the wizard's MCP card can open
	// the panel even while the backend is still starting.
	const openMcpPanel = vscode.commands.registerCommand('orion.openMcpPanel', () => {
		McpPanel.createOrShow();
	});
	context.subscriptions.push(openMcpPanel);

	// WAIT for the backend to be ready before continuing
	await ensureBackendRunning();
}

export function deactivate() { }
