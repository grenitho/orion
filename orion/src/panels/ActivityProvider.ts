import * as vscode from 'vscode';

export class ActivityItem extends vscode.TreeItem {
    constructor(
        public readonly label: string,
        public readonly description?: string,
        public readonly iconPath?: vscode.ThemeIcon,
        public readonly command?: vscode.Command
    ) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.description = description;
        this.iconPath = iconPath;
        this.command = command;
    }
}

export class ActivityProvider implements vscode.TreeDataProvider<ActivityItem> {
    private _onDidChangeTreeData = new vscode.EventEmitter<ActivityItem | undefined>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private items: ActivityItem[] = [];

    constructor() {
        this.items = [
            new ActivityItem(
                'Click to open the Chat Panel',
                undefined,
                new vscode.ThemeIcon('comment-discussion'),
                { command: 'orion.openChat', title: 'Open Chat' }
            ),
            new ActivityItem('Waiting for commands...', 'Ready', new vscode.ThemeIcon('circle-outline'))
        ];
    }

    refresh() {
        this._onDidChangeTreeData.fire(undefined);
    }

    addActivity(label: string, description: string) {
        this.items.push(new ActivityItem(label, description, new vscode.ThemeIcon('sync~spin')));
        this.refresh();
    }

    clearActivities() {
        this.items = [
            new ActivityItem(
                'Click to open the Chat Panel',
                undefined,
                new vscode.ThemeIcon('comment-discussion'),
                { command: 'orion.openChat', title: 'Open Chat' }
            )
        ];
        this.refresh();
    }

    getTreeItem(element: ActivityItem): vscode.TreeItem {
        return element;
    }

    getChildren(): ActivityItem[] {
        return this.items;
    }
}