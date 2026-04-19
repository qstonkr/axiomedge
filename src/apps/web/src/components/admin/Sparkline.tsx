/**
 * Tiny SVG sparkline — no charting deps. data: 시계열 점들의 raw 값 array.
 * 빈 배열 / 1점 이하면 빈 placeholder.
 */
export function Sparkline({
  points,
  width = 100,
  height = 28,
  className = "",
}: {
  points: number[];
  width?: number;
  height?: number;
  className?: string;
}) {
  if (points.length < 2) {
    return (
      <div
        aria-hidden
        className={className}
        style={{
          width,
          height,
          background:
            "repeating-linear-gradient(90deg, var(--color-border-default) 0 1px, transparent 1px 6px)",
          opacity: 0.3,
        }}
      />
    );
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const stepX = width / (points.length - 1);
  const path = points
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      role="img"
      aria-label={`${points.length}개 시점 추이`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
