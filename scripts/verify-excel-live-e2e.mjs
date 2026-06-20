const base = "http://127.0.0.1:19532";
const headers = {
  Authorization: "Bearer dev-token",
  "Content-Type": "application/json",
};
const workbookId = "C:\\Users\\asdjj\\PrivateClaw\\Workspace\\text_1.xlsx";
const sheetName = "Sheet1";

async function post(path, body) {
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return JSON.parse(text);
}

async function runCommand(message) {
  let out = await post("/excel-live/command", {
    message,
    workbook_id: workbookId,
    sheet_name: sheetName,
    approve: false,
  });
  if (out.approval_required && out.pending_approval?.approval_id) {
    out = await post("/excel-live/approval", {
      approval_id: out.pending_approval.approval_id,
      approved: true,
    });
  }
  return out;
}

async function runAction(action, params = {}) {
  return post("/excel-live/action", {
    action,
    params,
    workbook_id: workbookId,
    sheet_name: sheetName,
    approve: true,
  });
}

async function read(rangeRef) {
  return runAction("excel_live.read_range", { range_ref: rangeRef });
}

function record(results, step, command, pass, detail) {
  results.push({ step, command, pass: Boolean(pass), detail });
}

async function main() {
  // 검증 안정화를 위한 데이터 시드
  await runAction("excel_live.write_range", {
    start_cell: "A1",
    values_2d: [[10], [20], [30], [40], [50], [60], [70], [80], [90], [100]],
  });
  await runAction("excel_live.write_range", {
    start_cell: "D1",
    values_2d: [[5], [2], [0], [-1], [-2], [3], [4], [-5], [6], [-7]],
  });

  const results = [];

  let out = await runCommand("열린 통합문서 목록 보여줘");
  record(
    results,
    "1",
    "열린 통합문서 목록 보여줘",
    out.action === "excel_live.list_workbooks" &&
      Array.isArray(out.result?.workbooks) &&
      out.result.workbooks.length >= 1,
    out.action,
  );

  out = await runCommand("A1:C10 조회해줘");
  record(
    results,
    "2",
    "A1:C10 조회해줘",
    out.action === "excel_live.read_range" &&
      out.result?.row_count === 10 &&
      out.result?.col_count === 3,
    `${out.result?.row_count}x${out.result?.col_count}`,
  );

  await runCommand("C3에 120 입력해줘");
  out = await read("C3");
  record(
    results,
    "3",
    "C3에 120 입력해줘",
    out.result?.values?.[0]?.[0] === 120,
    `C3=${out.result?.values?.[0]?.[0]}`,
  );

  out = await runCommand("B9 값만 읽어줘");
  record(
    results,
    "4",
    "B9 값만 읽어줘",
    out.action === "excel_live.read_range" &&
      out.result?.row_count === 1 &&
      out.result?.col_count === 1,
    `B9=${out.result?.values?.[0]?.[0]}`,
  );

  await runCommand("B2:D2에 이름,수량,금액 입력");
  out = await read("B2:D2");
  const b2d2 = out.result?.values?.[0] ?? [];
  record(
    results,
    "5",
    "B2:D2에 이름,수량,금액 입력",
    b2d2[0] === "이름" && b2d2[1] === "수량" && b2d2[2] === "금액",
    b2d2.join(","),
  );

  await runCommand("H8 999 set");
  out = await read("H8");
  record(
    results,
    "6",
    "H8 999 set",
    out.result?.values?.[0]?.[0] === 999,
    `H8=${out.result?.values?.[0]?.[0]}`,
  );

  out = await runCommand("A열에서 50 이상인 셀만 노란색 배경 적용");
  record(
    results,
    "7",
    "A열에서 50 이상인 셀만 노란색 배경 적용",
    out.action === "excel_live.highlight_by_condition" &&
      Number(out.result?.changed_cells ?? 0) >= 1,
    `changed=${out.result?.changed_cells}`,
  );

  out = await runCommand("D:D 컬럼에서 0 이하 숫자는 파란색 표시");
  record(
    results,
    "8",
    "D:D 컬럼에서 0 이하 숫자는 파란색 표시",
    out.action === "excel_live.highlight_by_condition" &&
      Number(out.result?.changed_cells ?? 0) >= 1,
    `changed=${out.result?.changed_cells}`,
  );

  await runCommand("J1에 수식 =SUM(A1:A10) 적용");
  out = await read("J1");
  record(
    results,
    "9",
    "J1에 수식 =SUM(A1:A10) 적용",
    out.result?.values?.[0]?.[0] === 550,
    `J1=${out.result?.values?.[0]?.[0]}`,
  );

  await runCommand('K2:K20에 formula =IF(A2>0,"Y","N") set');
  out = await read("K2");
  record(
    results,
    "10",
    'K2:K20에 formula =IF(A2>0,"Y","N") set',
    out.result?.values?.[0]?.[0] === "Y",
    `K2=${out.result?.values?.[0]?.[0]}`,
  );

  out = await runAction("excel_live.save_workbook", {});
  record(
    results,
    "save",
    "save_workbook",
    out.ok === true && out.result?.saved === true,
    out.result?.full_path ?? "",
  );

  const failed = results.filter((x) => !x.pass);
  const summary = {
    total: results.length,
    passed: results.length - failed.length,
    failed: failed.length,
    results,
  };
  console.log(JSON.stringify(summary, null, 2));

  if (failed.length > 0) {
    process.exit(2);
  }
}

main().catch((err) => {
  console.error(err?.stack || String(err));
  process.exit(1);
});
