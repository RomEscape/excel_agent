import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import '../theme/brand_palette.dart';

/// 앱바 등에 놓는 김대리 워드마크.
///
/// 원본 SVG는 라이트 지면 기준이라 그라디언트의 어두운 끝(#0B3F0A·#015F00)이
/// 다크 배경에서 거의 사라진다. 그래서 다크에서는 밝은 브랜드 초록 한 색으로 눕혀 쓴다.
/// (다크 전용 워드마크 에셋을 새로 만들면 새 브랜드 색을 지어내야 하므로 택하지 않았다.)
class BrandWordmark extends StatelessWidget {
  const BrandWordmark({super.key, this.height = 26});

  final double height;

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return SvgPicture.asset(
      'assets/brand-wordmark.svg',
      height: height,
      semanticsLabel: '김대리',
      colorFilter: isDark
          ? const ColorFilter.mode(BrandPalette.lift, BlendMode.srcIn)
          : null,
    );
  }
}
