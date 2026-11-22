// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SecondsInput } from "../src/EventSettings";
afterEach(cleanup);
describe("Ввод модельных секунд", () => {
  it("принимает десятичную запятую без потери миллисекунд", () => {
    const changed = vi.fn();
    render(<SecondsInput value={1000} label="Время" onChange={changed} />);
    const input = screen.getByLabelText("Время");
    fireEvent.change(input, { target: { value: "1,234" } });
    fireEvent.blur(input);
    expect(changed).toHaveBeenCalledWith(1234);
  });
  it("отклоняет значение точнее миллисекунды и пустой ввод", () => {
    const changed = vi.fn();
    render(<SecondsInput value={1000} label="Время" onChange={changed} />);
    const input = screen.getByLabelText("Время");
    fireEvent.change(input, { target: { value: "0.0001" } });
    fireEvent.blur(input);
    expect(changed).not.toHaveBeenCalled();
    expect(screen.getByText(/точностью до/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(changed).not.toHaveBeenCalled();
  });
  it("отменяет неподтверждённый ввод по Escape", () => {
    const changed = vi.fn();
    render(<SecondsInput value={1200} label="Время" onChange={changed} />);
    const input = screen.getByLabelText("Время") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "3,5" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("1.2");
    expect(changed).not.toHaveBeenCalled();
  });
});
