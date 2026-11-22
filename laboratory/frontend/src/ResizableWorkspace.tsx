import { Children, useState, type ReactNode } from "react";

const DEFAULT = 300;
const MIN = 260;
const maximum = () => Math.min(640, Math.max(MIN, window.innerWidth - 640));
const clamp = (value: number) => Math.max(MIN, Math.min(maximum(), value));

export function ResizableWorkspace({ children }: { children: ReactNode }) {
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(localStorage.getItem("cp1.parameters.width"));
      return clamp(saved || DEFAULT);
    } catch {
      return DEFAULT;
    }
  });
  const parts = Children.toArray(children);
  const change = (value: number) => {
    const next = clamp(value);
    setWidth(next);
    try {
      localStorage.setItem("cp1.parameters.width", String(next));
    } catch {
      /* Storage is optional. */
    }
  };
  return (
    <div
      className="main-grid"
      style={{ gridTemplateColumns: `${width}px 12px minmax(0, 1fr)` }}
    >
      {parts[0]}
      <div
        className="sidebar-resizer"
        role="separator"
        tabIndex={0}
        aria-label="Ширина панели параметров"
        aria-orientation="vertical"
        aria-valuemin={MIN}
        aria-valuemax={maximum()}
        aria-valuenow={width}
        onDoubleClick={() => change(DEFAULT)}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          e.currentTarget.dataset.startX = String(e.clientX);
          e.currentTarget.dataset.startWidth = String(width);
          document.body.classList.add("resizing-sidebar");
        }}
        onPointerMove={(e) => {
          if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
          change(
            Number(e.currentTarget.dataset.startWidth) +
              e.clientX -
              Number(e.currentTarget.dataset.startX),
          );
        }}
        onPointerUp={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId))
            e.currentTarget.releasePointerCapture(e.pointerId);
          document.body.classList.remove("resizing-sidebar");
        }}
        onLostPointerCapture={() =>
          document.body.classList.remove("resizing-sidebar")
        }
        onKeyDown={(e) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
            e.preventDefault();
            change(
              e.key === "Home"
                ? DEFAULT
                : e.key === "End"
                  ? maximum()
                  : width + (e.key === "ArrowRight" ? 16 : -16),
            );
          }
        }}
      />
      {parts[1]}
    </div>
  );
}
