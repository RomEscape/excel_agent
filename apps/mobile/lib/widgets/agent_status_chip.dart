import 'package:flutter/material.dart';

import '../protocol/protocol.dart';
import '../theme/agent_status_tokens.dart';
import '../theme/brand_theme.dart';

/// 데스크톱의 [AgentState]를 그대로 비추는 상단 칩.
///
/// 색·라벨은 전부 테마 확장([AgentStatusColors])과 토큰에서 온다 — 여기서 색을 짓지 않는다.
class AgentStatusChip extends StatelessWidget {
  const AgentStatusChip({
    super.key,
    required this.connected,
    required this.agentState,
  });

  final bool connected;
  final AgentState agentState;

  @override
  Widget build(BuildContext context) {
    final colors =
        Theme.of(context).extension<AgentStatusColors>() ??
        AgentStatusColors.light;
    final token = resolveAgentStatus(
      state: agentState,
      connected: connected,
      colors: colors,
    );
    return Chip(
      avatar: CircleAvatar(backgroundColor: token.color, radius: 6),
      label: Text(token.label),
      visualDensity: VisualDensity.compact,
    );
  }
}
