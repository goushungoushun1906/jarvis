import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

export type CoreState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline';

const PARTICLE_COUNT = 120;

// Animation speed multipliers per state
const STATE_SPEEDS: Record<CoreState, number> = {
  idle: 0.5,
  listening: 1.0,
  thinking: 1.5,
  speaking: 0.8,
  error: 0.3,
  offline: 0.1,
};

// Color per state (THREE.Color objects)
const STATE_COLORS: Record<CoreState, string> = {
  idle: '#60a5fa',
  listening: '#93c5fd',
  thinking: '#a78bfa',
  speaking: '#67e8f9',
  error: '#f87171',
  offline: '#94a3b8',
};

interface ParticleFieldProps {
  state: CoreState;
}

export default function ParticleField({ state }: ParticleFieldProps) {
  const pointsRef = useRef<THREE.Points>(null!);

  // Generate particle positions on a spherical shell
  const { positions, basePositions, velocities, seeds } = useMemo(() => {
    const pos = new Float32Array(PARTICLE_COUNT * 3);
    const base = new Float32Array(PARTICLE_COUNT * 3);
    const vel = new Float32Array(PARTICLE_COUNT); // angular speed seed
    const sd = new Float32Array(PARTICLE_COUNT); // random seed

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      // Fibonacci sphere distribution for even spacing
      const phi = Math.acos(1 - (2 * (i + 0.5)) / PARTICLE_COUNT);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;

      const r = 1.8 + Math.random() * 0.6;
      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.sin(phi) * Math.sin(theta);
      const z = r * Math.cos(phi);

      pos[i * 3] = x;
      pos[i * 3 + 1] = y;
      pos[i * 3 + 2] = z;

      base[i * 3] = x;
      base[i * 3 + 1] = y;
      base[i * 3 + 2] = z;

      vel[i] = 0.3 + Math.random() * 0.7; // speed variation
      sd[i] = Math.random() * Math.PI * 2; // phase offset
    }

    return { positions: pos, basePositions: base, velocities: vel, seeds: sd };
  }, []);

  // Colors buffer
  const colorArray = useMemo(() => {
    return new Float32Array(PARTICLE_COUNT * 3);
  }, []);

  useFrame((_, delta) => {
    if (!pointsRef.current) return;

    const geo = pointsRef.current.geometry;
    const posAttr = geo.getAttribute('position') as THREE.BufferAttribute;
    const colorAttr = geo.getAttribute('color') as THREE.BufferAttribute;
    const posArray = posAttr.array as Float32Array;
    const colArray = colorAttr.array as Float32Array;

    const speed = STATE_SPEEDS[state];
    const targetColor = new THREE.Color(STATE_COLORS[state]);
    const time = performance.now() * 0.001;

    const isSpeaking = state === 'speaking';
    const isError = state === 'error';
    const isOffline = state === 'offline';

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const bx = basePositions[i * 3];
      const by = basePositions[i * 3 + 1];
      const bz = basePositions[i * 3 + 2];

      // Orbit around Y axis
      const angle = seeds[i] + time * speed * velocities[i] * 0.3;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);

      let nx = bx * cos - bz * sin;
      let ny = by;
      let nz = bx * sin + bz * cos;

      // Speaking: pulse outward rhythmically
      if (isSpeaking) {
        const pulse = Math.sin(time * 6 + seeds[i]) * 0.3;
        const dist = Math.sqrt(nx * nx + ny * ny + nz * nz);
        const norm = dist > 0.001 ? 1 / dist : 0;
        nx += nx * norm * pulse;
        ny += ny * norm * pulse;
        nz += nz * norm * pulse;
      }

      // Error: erratic movement
      if (isError) {
        const flicker = Math.sin(time * 15 + seeds[i] * 10) > 0 ? 0.15 : -0.15;
        nx += flicker;
        ny += Math.cos(time * 12 + seeds[i] * 7) * 0.1;
        nz += Math.sin(time * 18 + seeds[i] * 5) * 0.1;
      }

      // Offline: pull inward
      if (isOffline) {
        nx *= 0.7;
        ny *= 0.7;
        nz *= 0.7;
      }

      posArray[i * 3] = nx;
      posArray[i * 3 + 1] = ny;
      posArray[i * 3 + 2] = nz;

      // Color: lerp toward target color
      colArray[i * 3] = targetColor.r;
      colArray[i * 3 + 1] = targetColor.g;
      colArray[i * 3 + 2] = targetColor.b;
    }

    posAttr.needsUpdate = true;
    colorAttr.needsUpdate = true;
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          args={[positions, 3]}
          count={PARTICLE_COUNT}
          itemSize={3}
        />
        <bufferAttribute
          attach="attributes-color"
          args={[colorArray, 3]}
          count={PARTICLE_COUNT}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.035}
        vertexColors
        transparent
        opacity={state === 'offline' ? 0.2 : 0.9}
        sizeAttenuation
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
