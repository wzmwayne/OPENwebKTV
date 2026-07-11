class WsClient {
    constructor(url, onMessage) {
        this.url = url;
        this.onMessage = onMessage;
        this.ws = null;
        this.reconnectTimer = null;
        this.connect();
    }

    connect() {
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        this.ws = new WebSocket(`${scheme}//${host}${this.url}`);
        this.ws.onopen = () => {
            console.log('WS connected');
            if (this.onConnect) this.onConnect();
        };
        this.ws.onclose = () => {
            console.log('WS disconnected, reconnect in 3s...');
            this.reconnectTimer = setTimeout(() => this.connect(), 3000);
        };
        this.ws.onerror = (e) => console.error('WS error', e);
        this.ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (this.onMessage) this.onMessage(data);
            } catch (err) {
                console.error('WS parse error', err);
            }
        };
    }

    send(msg) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(msg));
        }
    }

    close() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.ws) this.ws.close();
    }
}
