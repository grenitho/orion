import * as vscode from 'vscode';
import { BackendService } from './services/backendService';
import { ChatPanel } from './panels/ChatPanel';
import { PerformancePanel } from './panels/PerformancePanel';
import { ActivityProvider } from './panels/ActivityProvider';
import { ensureBackendRunning } from './services/backendManager';


const backend = new BackendService();

export async function activate(context: vscode.ExtensionContext) {
	console.log('Orion Extension Active!');

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
}

export function deactivate() { }