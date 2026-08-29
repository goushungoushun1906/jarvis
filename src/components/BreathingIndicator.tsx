import type { JavisStatus } from "../lib/types";

interface BreathingIndicatorProps {
  status: JavisStatus;
}

export default function BreathingIndicator({ status }: BreathingIndicatorProps) {
  return <div className={`breathing-dot ${status}`} />;
}
