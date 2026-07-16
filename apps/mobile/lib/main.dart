import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'protocol/protocol.dart';
import 'store/chat_controller.dart';

void main() {
  runApp(const ProviderScope(child: OfficeClawApp()));
}

class OfficeClawApp extends StatelessWidget {
  const OfficeClawApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'officeclaw',
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      home: const ChatScreen(),
    );
  }
}

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _relayCtrl = TextEditingController(text: 'http://127.0.0.1:8787');
  final _pairingCtrl = TextEditingController();
  final _inputCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();

  @override
  void dispose() {
    _relayCtrl.dispose();
    _pairingCtrl.dispose();
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _connect() {
    final pid = _pairingCtrl.text.trim();
    if (pid.isEmpty) return;
    ref
        .read(chatControllerProvider.notifier)
        .connect(relayUrl: _relayCtrl.text.trim(), pairingId: pid);
  }

  void _send() {
    if (_inputCtrl.text.trim().isEmpty) return;
    ref.read(chatControllerProvider.notifier).sendMessage(_inputCtrl.text);
    _inputCtrl.clear();
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final st = ref.watch(chatControllerProvider);
    // 새 토큰이 도착할 때마다 하단으로 자동 스크롤
    ref.listen(chatControllerProvider, (_, _) => _scrollToBottom());

    return Scaffold(
      appBar: AppBar(
        title: const Text('officeclaw'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: _StatusChip(connected: st.connected, agentState: st.agentState),
          ),
        ],
      ),
      body: Column(
        children: [
          if (!st.connected) _pairingBar(),
          Expanded(
            child: ListView.builder(
              controller: _scrollCtrl,
              padding: const EdgeInsets.all(12),
              itemCount: st.messages.length,
              itemBuilder: (_, i) => _bubble(st.messages[i]),
            ),
          ),
          _inputBar(st.connected),
        ],
      ),
    );
  }

  Widget _pairingBar() {
    return Material(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: Row(
          children: [
            Expanded(
              flex: 3,
              child: TextField(
                controller: _relayCtrl,
                decoration: const InputDecoration(
                  labelText: 'relay URL',
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              flex: 4,
              child: TextField(
                controller: _pairingCtrl,
                decoration: const InputDecoration(
                  labelText: 'pairing_id (수동)',
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            FilledButton(onPressed: _connect, child: const Text('연결')),
          ],
        ),
      ),
    );
  }

  Widget _bubble(ChatMessage m) {
    final scheme = Theme.of(context).colorScheme;
    return Align(
      alignment: m.fromUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        decoration: BoxDecoration(
          color: m.fromUser
              ? scheme.primaryContainer
              : scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Text(m.streaming ? '${m.text} ▍' : m.text),
      ),
    );
  }

  Widget _inputBar(bool connected) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _inputCtrl,
                enabled: connected,
                onSubmitted: (_) => _send(),
                decoration: InputDecoration(
                  hintText: connected ? '메시지 입력' : '먼저 연결하세요',
                  border: const OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: connected ? _send : null,
              icon: const Icon(Icons.send),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final bool connected;
  final AgentState agentState;
  const _StatusChip({required this.connected, required this.agentState});

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (agentState) {
      AgentState.thinking => ('생각 중', Colors.orange),
      AgentState.remoteControlling => ('제어 중', Colors.blue),
      AgentState.offline => ('오프라인', Colors.grey),
      AgentState.idle => connected
          ? ('연결됨', Colors.green)
          : ('대기', Colors.grey),
    };
    return Chip(
      avatar: CircleAvatar(backgroundColor: color, radius: 6),
      label: Text(label),
      visualDensity: VisualDensity.compact,
    );
  }
}
