import { describe, it, expect } from "vitest";
import { number } from "../src/types";
describe("научные показатели", () => {
  it("различает неприменимость и ноль", () => {
    expect(number(null)).toBe("—");
    expect(number(0)).toBe("0");
  });
  it("показывает малые значения", () => expect(number(1e-12)).toBe("1.00e-12"));
});
