import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export type CoreState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline';

const RING_CONFIGS = [
  { radius: 1.6, tube: 0.012, tiltX: Math.PI * 0.08, tiltZ: 0, baseSpeed: 1.0 },
  { radius: 1.85, tube: 0.010, tiltX: Math.PI * 0.25, tiltZ: Math.PI * 0.15, baseSpeed: -0.7 },
  { radius: 2.1, tube: 0.008, tiltX: Math.PI * 0.42, tiltZ: -Math.PI * 0.1, baseSpeed: 0.5 },
];

// Rotation speed multiplier per state
const STATE_ROTATION_SPEED: Record<CoreState, number> = {
  idle: 0.2,
  listening: 0.5,
  thinking: 1.5,
  speaking: 0.8,
  error: 0.1,
  offline: 0.05,
};

// Emissive intensity per state
const STATE_INTENSITY: Record<CoreState, number> = {
  idle: 0.6,
  listening: 1.0,
  thinking: 1.5,
  speaking: 1.2,
  error: 1.8,
  offline: 0.15,
};

const STATE_COLORS: Record<CoreState, string> = {
  idle: '#3b82f6',
  listening: '#60a5fa',
  thinking: '#8b5cf6',
  speaking: '#22d3ee',
  error: '#ef4444',
  offline: '#64748b',
};

interface OrbitRingsProps {
  state: CoreState;
}

function SingleRing({
  radius,
  tube,
  tiltX,
  tiltZ,
  baseSpeed,
  state,
}: {
  radius: number;
  tube: number;
  tiltX: number;
  tiltZ: number;
  baseSpeed: number;
  state: CoreState;
}) {
  const groupRef = useRef<THREE.Group>(null!);
  const materialRef = useRef<THREE.MeshStandardMaterial>(null!);

  const color = useMemo(() => new THREE.Color(STATE_COLORS[state]), [state]);

  useFrame((_, delta) => {
    if (!groupRef.current) return;

    const speedMul = STATE_ROTATION_SPEED[state];
    const intensity = STATE_INTENSITY[state];
    const time = performance.now() * 0.001;

    // Rotate ring group around Y axis
    groupRef.current.rotation.y += baseSpeed * speedMul * delta;

    // Error: wobble
    if (state === 'error') {
      groupRef.current.rotation.x = tiltX + Math.sin(time * 8) * 0.05;
      groupRef.current.rotation.z = tiltZ + Math.cos(time * 6) * 0.04;
    } else {
      groupRef.current.rotation.x = tiltX;
      groupRef.current.rotation.z = tiltZ;
    }

    // Speaking: scale pulse
    if (state === 'speaking' && materialRef.current) {
      const pulse = 1.0 + Math.sin(time * 8) * 0.08;
      groupRef.current.scale.setScalar(pulse);
      materialRef.current.emissiveIntensity = intensity + Math.sin(time * 8) * 0.3;
    } else if (materialRef.current) {
      groupRef.current.scale.setScalar(1.0);
      materialRef.current.emissiveIntensity = intensity;
    }
  });

  return (
    <group ref={groupRef} rotation={[tiltX, 0, tiltZ]}>
      <mesh>
        <torusGeometry args={[radius, tube, 16, 128]} />
        <meshStandardMaterial
          ref={materialRef}
          color={color}
          emissive={color}
          emissiveIntensity={STATE_INTENSITY[state]}
          transparent
          opacity={state === 'offline' ? 0.15 : 0.55}
          side={THREE.DoubleSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  );
}

export default function OrbitRings({ state }: OrbitRingsProps) {
  return (
    <group>
      {RING_CONFIGS.map((cfg, i) => (
        <SingleRing key={i} {...cfg} state={state} />
      ))}
    </group>
  );
}
