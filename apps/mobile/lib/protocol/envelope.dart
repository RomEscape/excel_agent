import 'dart:convert';

import 'frames.dart';

const int protocolVersion = 1;

/// relay 라우팅용 봉투. relay는 라우팅 헤더만 보고 payload는 해석하지 않는다(content-blind).
class Envelope {
  final int v;
  final String pairingId;
  final Direction direction;
  final int seq;
  final Frame payload;

  const Envelope({
    this.v = protocolVersion,
    required this.pairingId,
    required this.direction,
    required this.seq,
    required this.payload,
  });

  Map<String, dynamic> toJson() => {
    'v': v,
    'pairing_id': pairingId,
    'direction': direction.wire,
    'seq': seq,
    'payload': payload.toJson(),
  };

  factory Envelope.fromJson(Map<String, dynamic> j) => Envelope(
    v: (j['v'] as int?) ?? protocolVersion,
    pairingId: j['pairing_id'] as String,
    direction: Direction.fromWire(j['direction'] as String),
    seq: j['seq'] as int,
    payload: Frame.fromJson(j['payload'] as Map<String, dynamic>),
  );

  /// WS로 보낼 JSON 텍스트.
  String encode() => jsonEncode(toJson());

  /// WS 텍스트 → Envelope.
  static Envelope decode(String raw) =>
      Envelope.fromJson(jsonDecode(raw) as Map<String, dynamic>);
}
