import 'package:flutter/material.dart';

import '../protocol/protocol.dart';
import 'brand_theme.dart';

/// 상태 칩 하나가 쓰는 표시 토큰.
@immutable
class AgentStatusToken {
  const AgentStatusToken({required this.label, required this.color});

  final String label;
  final Color color;
}

/// 프로토콜의 [AgentState](+연결 여부)를 표시 토큰으로 옮긴다.
///
/// 상태→라벨·색 결정은 **여기 한 곳**에서만 한다. 위젯 안에서 다시 분기하지 않는다.
AgentStatusToken resolveAgentStatus({
  required AgentState state,
  required bool connected,
  required AgentStatusColors colors,
}) {
  return switch (state) {
    AgentState.thinking => AgentStatusToken(
      label: '생각 중',
      color: colors.thinking,
    ),
    AgentState.remoteControlling => AgentStatusToken(
      label: '제어 중',
      color: colors.remoteControlling,
    ),
    AgentState.offline => AgentStatusToken(
      label: '오프라인',
      color: colors.inactive,
    ),
    AgentState.idle =>
      connected
          ? AgentStatusToken(label: '연결됨', color: colors.connected)
          : AgentStatusToken(label: '대기', color: colors.inactive),
  };
}
