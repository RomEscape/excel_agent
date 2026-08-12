/**
 * excelResult — Excel Live 실행 결과를 사람이 읽는 한 줄로 옮기는 순수 모듈.
 *
 * 예전에는 여기서 구조화된 뷰모델(표·막대·통계 카드)까지 만들었고
 * components/ui/result-card.jsx가 그걸 렌더했다. 최종 와이어프레임 14화면에는
 * 인라인 결과 카드가 없어서 카드 렌더를 걷어냈고, 이 모듈도 문장만 남겼다.
 *
 * 진행 표현은 툴 진행 스텝 칩(lib/toolSteps.js)이 대신한다 — 그쪽은 "무엇을
 * 했는지", 이쪽은 "결과가 무엇인지"를 말한다.
 *
 * 반환 문자열은 세 곳이 그대로 쓴다: 말풍선 본문 · 세션 영속화 · 메신저 전송.
 * 그래서 여기서 문구를 바꾸면 저장된 대화 기록의 톤도 같이 바뀐다.
 */

/**
 * Excel Live action + result → 표시 문장.
 *
 * @param {string} action
 * @param {Record<string, unknown>} result
 * @returns {string}
 */
export function formatResultText(action, result = {}) {
  if (!result || typeof result !== "object") {
    return "엑셀 작업이 완료되었습니다.";
  }

  switch (action) {
    case "excel_live.read_range": {
      const rowCount = Number(
        result.row_count || (Array.isArray(result.values) ? result.values.length : 0) || 0
      );
      const colCount = Number(
        result.col_count ||
          (Array.isArray(result.values?.[0]) ? result.values[0].length : 0) ||
          0
      );
      return `${result.address || ""} 범위를 읽었습니다 (${rowCount}행 × ${colCount}열).`;
    }

    case "excel_live.group_by_aggregate": {
      const groups = Array.isArray(result.groups) ? result.groups : [];
      const preview = groups
        .slice(0, 5)
        .map((g) => `${g?.key ?? ""}: ${g?.value ?? ""}`)
        .join(", ");
      return `${result.group_column || ""}별 ${result.agg || ""} — ${preview}${
        groups.length > 5 ? " …" : ""
      }`;
    }

    case "excel_live.calculate_column_stat":
      return `${result.header || result.column || ""} 열 ${result.stat || ""} = ${result.value}`;

    case "excel_live.list_workbooks": {
      const rows = Array.isArray(result.workbooks) ? result.workbooks : [];
      if (rows.length === 0) return "열려 있는 엑셀 통합문서가 없습니다.";
      return `열린 통합문서 ${rows.length}개: ${rows
        .map((r) => r.name || r.workbook_id)
        .join(", ")}`;
    }

    case "excel_live.write_range":
      return `${result.address || ""} 범위에 ${result.written_cells || 0}개 셀을 기록했습니다.`;
    case "excel_live.highlight_by_condition":
      return `${result.address || ""} 범위에서 ${result.changed_cells || 0}개 셀을 강조했습니다.`;
    case "excel_live.apply_border":
      return `${result.address || ""} 범위에 경계선을 적용했습니다 (${result.changed_cells || 0}개 셀).`;
    case "excel_live.set_formula":
      return `${result.address || ""} 범위에 수식을 적용했습니다 (${result.formula_applied_cells || 0}개 셀).`;
    case "excel_live.save_workbook":
      return `엑셀 파일을 저장했습니다 (${result.name || result.full_path || "현재 통합문서"}).`;
    case "excel_live.filter_rows":
      return `조건에 맞는 ${result.kept_rows || 0}개 행을 남기고 ${result.removed_rows || 0}개 행을 제거했습니다.`;
    case "excel_live.sort_rows":
      return `${result.column || ""} 기준으로 ${result.sorted_rows || 0}개 행을 정렬했습니다.`;
    case "excel_live.dedupe_rows":
      return `중복 ${result.removed_duplicates || 0}개 행을 제거했습니다 (${result.kept_rows || 0}개 유지).`;
    case "excel_live.drop_column":
      return `'${result.dropped_column || ""}' 열을 삭제했습니다.`;
    case "excel_live.rename_column":
      return `'${result.old_name || ""}' 열을 '${result.new_name || ""}'로 변경했습니다.`;
    case "excel_live.add_column":
      return `'${result.name || ""}' 열을 추가했습니다.`;
    default:
      return "엑셀 작업이 완료되었습니다.";
  }
}
