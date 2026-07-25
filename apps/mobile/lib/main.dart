import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_svg/flutter_svg.dart';

import 'pairing/pairing_screen.dart';
import 'pairing/pairing_service.dart';
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
      title: '김대리',
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
  final _codeCtrl = TextEditingController();
  final _inputCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();

  @override
  void dispose() {
    _relayCtrl.dispose();
    _pairingCtrl.dispose();
    _codeCtrl.dispose();
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  /// 수동 페어링(개발용).
  ///
  /// code가 있으면 QR 스캔과 동일하게 `/pair/complete`로 **바인딩까지** 하고 연결한다.
  /// (relay는 바인딩 안 된 pairing_id를 4401로 거부하므로 이 단계가 없으면 연결이 안 된다.)
  /// code가 비어 있으면 이미 바인딩된 세션에 재연결하는 것으로 본다.
  Future<void> _connect() async {
    final relay = _relayCtrl.text.trim();
    final pid = _pairingCtrl.text.trim();
    final code = _codeCtrl.text.trim();
    if (relay.isEmpty || pid.isEmpty) return;

    if (code.isNotEmpty) {
      try {
        await completePairing(
          PairingInfo(relayUrl: relay, pairingId: pid, code: code),
        );
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(
            context,
          ).showSnackBar(SnackBar(content: Text('페어링 실패: $e')));
        }
        return;
      }
    }
    if (!mounted) return;
    ref
        .read(chatControllerProvider.notifier)
        .connect(relayUrl: relay, pairingId: pid);
  }

  Future<void> _scanQr() async {
    final info = await Navigator.of(context).push<PairingInfo>(
      MaterialPageRoute(builder: (_) => const PairingScreen()),
    );
    if (info == null) return;
    try {
      await completePairing(info);
      if (!mounted) return;
      ref
          .read(chatControllerProvider.notifier)
          .connect(relayUrl: info.relayUrl, pairingId: info.pairingId);
    } on PairingException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
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
        title: SvgPicture.asset(
          'assets/brand-wordmark.svg',
          height: 26,
          semanticsLabel: '김대리',
        ),
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
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _scanQr,
                icon: const Icon(Icons.qr_code_scanner),
                label: const Text('QR로 데스크톱 연결'),
              ),
            ),
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(
                  flex: 4,
                  child: TextField(
                    controller: _relayCtrl,
                    decoration: const InputDecoration(
                      labelText: 'relay',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  flex: 4,
                  child: TextField(
                    controller: _pairingCtrl,
                    decoration: const InputDecoration(
                      labelText: 'pairing_id',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  flex: 2,
                  child: TextField(
                    controller: _codeCtrl,
                    decoration: const InputDecoration(
                      labelText: 'code',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 6),
                OutlinedButton(onPressed: _connect, child: const Text('연결')),
              ],
            ),
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(
                '수동(개발용): 데스크톱 QR 화면의 pairing_id + code를 넣으면 페어링까지 됩니다.',
                style: TextStyle(fontSize: 11),
              ),
            ),
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
