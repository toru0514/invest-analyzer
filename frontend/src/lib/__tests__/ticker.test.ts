import { describe, it, expect } from "vitest";
import { asTicker, isCompleteCode } from "@/lib/ticker";

describe("asTicker", () => {
  it("4桁数字コードを .T 付きに正規化する", () => {
    expect(asTicker("5803")).toBe("5803.T");
    expect(asTicker("7203")).toBe("7203.T");
  });
  it("英字入りの新形式コード（例: 285A / 130A）も受け付ける", () => {
    expect(asTicker("285A")).toBe("285A.T");
    expect(asTicker("130A")).toBe("130A.T");
  });
  it("小文字・前後空白・.T 付き入力を正規化する", () => {
    expect(asTicker(" 285a ")).toBe("285A.T");
    expect(asTicker("5803.t")).toBe("5803.T");
    expect(asTicker("285A.T")).toBe("285A.T");
  });
  it("1〜3桁の数字（部分入力）も一応コード扱いする（既存挙動を維持）", () => {
    expect(asTicker("58")).toBe("58.T");
  });
  it("名前・米株表記・桁あふれ・数字始まりでない入力は null", () => {
    expect(asTicker("フジクラ")).toBeNull();
    expect(asTicker("")).toBeNull();
    expect(asTicker("12345")).toBeNull(); // 5桁は不可
    expect(asTicker("ABCD")).toBeNull(); // 数字始まりでない
    expect(asTicker("AAPL")).toBeNull(); // 米株表記は対象外
  });
});

describe("isCompleteCode", () => {
  it("4桁の完全なコード（数字/英字入り・.T 任意）で true", () => {
    expect(isCompleteCode("5803")).toBe(true);
    expect(isCompleteCode("285A")).toBe(true);
    expect(isCompleteCode("285a")).toBe(true);
    expect(isCompleteCode("5803.T")).toBe(true);
  });
  it("部分入力(1〜3桁)や名前では false（名前解決リクエストを投げないため）", () => {
    expect(isCompleteCode("5")).toBe(false);
    expect(isCompleteCode("58")).toBe(false);
    expect(isCompleteCode("580")).toBe(false);
    expect(isCompleteCode("フジクラ")).toBe(false);
    expect(isCompleteCode("")).toBe(false);
  });
});
