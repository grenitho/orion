import * as vscode from 'vscode';
import { BackendService } from './services/backendService';
import { ChatPanel } from './panels/ChatPanel';
import { PerformancePanel } from './panels/PerformancePanel';
import { ActivityProvider } from './panels/ActivityProvider';
import { ensureBackendRunning, initSetupGuide, getSetupGuideUrl } from './services/backendManager';


const backend = new BackendService();

export async function activate(context: vscode.ExtensionContext) {
	console.log('Orion Extension Active!');

	// >>> UPDATE-010: derive the setup-guide URL from this extension's
	// package.json repository metadata before anything can link to it.
	initSetupGuide(context);

	// WAIT for the backend to be ready before continuing
	await ensureBackendRunning();

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
	context.subscriptions.push(startBackend);

	const openSetupGuide = vscode.commands.registerCommand('orion.openSetupGuide', async () => {
		const url = getSetupGuideUrl();
		if (url) {
			await vscode.env.openExternal(vscode.Uri.parse(url));
		} else {
			vscode.window.showInformationMessage('Orion: the setup guide lives at docs/PANDUAN_SETUP.md in the Orion repository.');
		}
	});
	context.subscriptions.push(openSetupGuide);
}

export function deactivate() { }