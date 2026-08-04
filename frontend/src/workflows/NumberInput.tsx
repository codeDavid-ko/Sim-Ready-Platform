"use client";

import { useEffect, useRef, useState } from "react";

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
};

/**
 * 숫자 입력 공용 컴포넌트.
 * 기존 `<input type="number" value={num} onChange={e=>set(Number(e.target.value))}>` 패턴은
 * 입력 도중의 중간 상태(빈칸·"-"·"0."·선행 0)를 즉시 숫자로 바꿔버려서
 *   - 0 에서 "-"(음수) 가 안 찍히고
 *   - "0" 이 안 지워져 "018" 처럼 됨
 * 이를 막기 위해 내부적으로 '문자열'을 들고 있다가 파싱되면 숫자를 emit 한다.
 * type=text + inputMode=decimal 로 브라우저 number 입력의 강제 정규화도 회피.
 * min/max 는 타이핑 중엔 강제하지 않고 blur 시 클램프(입력이 막히지 않게).
 */
export default function NumberInput({ value, onChange, min, max, onBlur, onFocus, ...rest }: Props) {
  const [text, setText] = useState<string>(() => (Number.isFinite(value) ? String(value) : ""));
  const focused = useRef(false);

  // 포커스가 아닐 때만 외부 value 변경을 표시에 반영(타이핑 중엔 건드리지 않음).
  useEffect(() => {
    if (focused.current) return;
    const cur = parseFloat(text);
    if (cur !== value || Number.isNaN(cur)) setText(Number.isFinite(value) ? String(value) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const t = e.target.value;
    // 숫자 입력 중간상태 허용: "", "-", ".", "-.", "12", "-1.5" …
    if (t !== "" && !/^-?\d*\.?\d*$/.test(t)) return; // 그 외 문자는 무시
    setText(t);
    const n = parseFloat(t);
    if (!Number.isNaN(n)) onChange(n); // 파싱되면 즉시 반영(실시간 미리보기 등)
  }

  function handleBlur(e: React.FocusEvent<HTMLInputElement>) {
    focused.current = false;
    let n = parseFloat(text);
    if (Number.isNaN(n)) n = Number.isFinite(value) ? value : 0;
    if (typeof min === "number" && n < min) n = min;
    if (typeof max === "number" && n > max) n = max;
    setText(String(n));
    onChange(n);
    onBlur?.(e);
  }

  return (
    <input
      type="text"
      inputMode="decimal"
      value={text}
      onFocus={(e) => { focused.current = true; onFocus?.(e); }}
      onChange={handleChange}
      onBlur={handleBlur}
      {...rest}
    />
  );
}
