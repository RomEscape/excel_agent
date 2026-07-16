import 'package:flutter_test/flutter_test.dart';
import 'package:officeclaw_mobile/protocol/protocol.dart';

void main() {
  test('Envelope + TokenDelta round-trip', () {
    final env = Envelope(
      pairingId: 'p1',
      direction: Direction.toMobile,
      seq: 7,
      payload: const TokenDelta(streamId: 's1', index: 0, text: '안녕'),
    );
    final back = Envelope.decode(env.encode());
    expect(back.pairingId, 'p1');
    expect(back.seq, 7);
    expect(back.direction, Direction.toMobile);
    expect(back.payload, isA<TokenDelta>());
    final p = back.payload as TokenDelta;
    expect(p.text, '안녕');
    expect(p.index, 0);
  });

  test('ChatUserMsg 인코딩 형태가 계약과 일치', () {
    final env = Envelope(
      pairingId: 'p1',
      direction: Direction.toDesktop,
      seq: 1,
      payload: const ChatUserMsg(clientMsgId: 'm1', text: 'hi'),
    );
    final j = env.toJson();
    expect((j['payload'] as Map)['type'], 'chat_user_msg');
    expect((j['payload'] as Map)['client_msg_id'], 'm1');
    expect(j['direction'], 'to_desktop');
  });

  test('AgentStatus 전 상태 round-trip', () {
    for (final s in AgentState.values) {
      final back = Frame.fromJson(AgentStatus(state: s).toJson()) as AgentStatus;
      expect(back.state, s);
    }
  });

  test('remote_controlling 와이어 문자열 매핑', () {
    expect(AgentState.remoteControlling.wire, 'remote_controlling');
    expect(AgentState.fromWire('remote_controlling'), AgentState.remoteControlling);
  });

  test('알 수 없는 frame type은 예외', () {
    expect(() => Frame.fromJson({'type': 'nope'}), throwsFormatException);
  });
}
