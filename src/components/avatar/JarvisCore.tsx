import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import ParticleField from './ParticleField';
import OrbitRings from './OrbitRings';

export type CoreState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline';

// === State color palettes ===
const STATE_COLORS = {
  idle: {
    core: '#2563eb',
    emissive: '#1d4ed8',
    glow: '#3b82f6',
    light: '#60a5fa',
  },
  listening: {
    core: '#3b82f6',
    emissive: '#2563eb',
    glow: '#60a5fa',
    light: '#93c5fd',
  },
  thinking: {
    core: '#7c3aed',
    emissive: '#6d28d9',
    glow: '#8b5cf6',
    light: '#a78bfa',
  },
  speaking: {
    core: '#06b6d4',
    emissive: '#0891b2',
    glow: '#22d3ee',
    light: '#67e8f9',
  },
  error: {
    core: '#dc2626',
    emissive: '#b91c1c',
    glow: '#ef4444',
    light: '#f87171',
  },
  offline: {
    core: '#475569',
    emissive: '#334155',
    glow: '#64748b',
    light: '#94a3b8',
  },
};

// Animation parameters per state
const STATE_ANIM = {
  idle: { rotationSpeed: 0.2, pulseSpeed: 0.3, pulseAmp: 0.06 },
  listening: { rotationSpeed: 0.4, pulseSpeed: 0.7, pulseAmp: 0.12 },
  thinking: { rotationSpeed: 1.2, pulseSpeed: 1.5, pulseAmp: 0.04 },
  speaking: { rotationSpeed: 0.6, pulseSpeed: 1.0, pulseAmp: 0.1 },
  error: { rotationSpeed: 0.1, pulseSpeed: 2.0, pulseAmp: 0.15 },
  offline: { rotationSpeed: 0.05, pulseSpeed: 0.1, pulseAmp: 0.01 },
};

interface JarvisCoreProps {
  state: CoreState;
  emotion?: 'happy' | 'focused' | 'confused' | 'neutral';
  size?: number;
}

export default function JarvisCore({ state, emotion = 'neutral', size = 1 }: JarvisCoreProps) {
  const groupRef = useRef<THREE.Group>(null!);
  const coreRef = useRef<THREE.Mesh>(null!);
  const glowRef = useRef<THREE.Mesh>(null!);
  const innerCoreRef = useRef<THREE.Mesh>(null!);
  const lightRef = useRef<THREE.PointLight>(null!);
  const coreMatRef = useRef<THREE.MeshPhysicalMaterial>(null!);
  const glowMatRef = useRef<THREE.MeshBasicMaterial>(null!);
  const innerMatRef = useRef<THREE.MeshBasicMaterial>(null!);

  // Pre-create color objects
  const colors = useMemo(() => {
    const s = STATE_COLORS[state];
    return {
      core: new THREE.Color(s.core),
      emissive: new THREE.Color(s.emissive),
      glow: new THREE.Color(s.glow),
      light: new THREE.Color(s.light),
    };
  }, [state]);

  const anim = STATE_ANIM[state];

  useFrame((_, delta) => {
    if (!groupRef.current) return;

    const time = performance.now() * 0.001;
    const s = STATE_COLORS[state];
    const coreColor = new THREE.Color(s.core);
    const emissiveColor = new THREE.Color(s.emissive);
    const glowColor = new THREE.Color(s.glow);
    const lightColor = new THREE.Color(s.light);

    // === Overall rotation ===
    groupRef.current.rotation.y += anim.rotationSpeed * delta;

    // Error: add wobble
    if (state === 'error') {
      groupRef.current.rotation.x = Math.sin(time * 5) * 0.03;
      groupRef.current.rotation.z = Math.cos(time * 3.7) * 0.02;
    } else {
      groupRef.current.rotation.x *= 0.95;
      groupRef.current.rotation.z *= 0.95;
    }

    // === Pulse calculation ===
    let pulse: number;
    if (state === 'error') {
      // Flickering for error
      pulse = Math.sin(time * anim.pulseSpeed * Math.PI * 2) > 0
        ? Math.sin(time * anim.pulseSpeed * Math.PI * 2)
        : -0.1 + Math.random() * 0.05;
    } else {
      pulse = Math.sin(time * anim.pulseSpeed * Math.PI * 2) * anim.pulseAmp;
    }

    // === Core sphere ===
    if (coreMatRef.current) {
      coreMatRef.current.color.lerp(coreColor, 0.05);
      coreMatRef.current.emissive.lerp(emissiveColor, 0.05);

      // Emotion adjustments
      if (state === 'idle') {
        switch (emotion) {
          case 'happy':
            coreMatRef.current.emissiveIntensity = 0.6 + Math.sin(time * 1.5) * 0.2;
            break;
          case 'focused':
            coreMatRef.current.emissiveIntensity = 0.8;
            break;
          case 'confused':
            coreMatRef.current.emissiveIntensity = 0.3 + Math.sin(time * 3) * 0.15;
            break;
          default:
            coreMatRef.current.emissiveIntensity = 0.5 + pulse;
        }
      } else {
        coreMatRef.current.emissiveIntensity = 0.8 + pulse;
      }

      // Thinking: stronger emissive
      if (state === 'thinking') {
        coreMatRef.current.emissiveIntensity = 1.5 + Math.sin(time * 2) * 0.3;
      }
    }

    if (coreRef.current) {
      const scale = 1.0 + pulse * 0.5;
      coreRef.current.scale.setScalar(scale);
    }

    // === Glow halo sphere ===
    if (glowMatRef.current) {
      glowMatRef.current.color.lerp(glowColor, 0.05);

      if (state === 'offline') {
        glowMatRef.current.opacity = 0.03;
      } else if (state === 'error') {
        glowMatRef.current.opacity = 0.12 + Math.sin(time * 10) * 0.05;
      } else if (state === 'listening') {
        glowMatRef.current.opacity = 0.2 + pulse * 0.5;
      } else {
        glowMatRef.current.opacity = 0.1 + pulse * 0.3;
      }
    }

    if (glowRef.current) {
      const glowScale = 1.35 + pulse * 2;
      glowRef.current.scale.setScalar(glowScale);
    }

    // === Inner core ===
    if (innerMatRef.current) {
      innerMatRef.current.color.lerp(lightColor, 0.05);

      if (state === 'offline') {
        innerMatRef.current.opacity = 0.05;
      } else if (state === 'thinking') {
        innerMatRef.current.opacity = 0.6 + Math.sin(time * 3) * 0.3;
      } else if (state === 'listening') {
        innerMatRef.current.opacity = 0.35 + Math.sin(time * 2) * 0.15;
      } else if (state === 'error') {
        innerMatRef.current.opacity = Math.random() > 0.5 ? 0.3 : 0.1;
      } else {
        innerMatRef.current.opacity = 0.2 + pulse;
      }
    }

    if (innerCoreRef.current) {
      const innerScale = 0.45 + pulse * 1.5;
      innerCoreRef.current.scale.setScalar(innerScale);
    }

    // === Point light ===
    if (lightRef.current) {
      lightRef.current.color.lerp(lightColor, 0.05);

      if (state === 'offline') {
        lightRef.current.intensity = 0.3;
      } else if (state === 'thinking') {
        lightRef.current.intensity = 2.5 + Math.sin(time * 2.5) * 0.5;
      } else if (state === 'listening') {
        lightRef.current.intensity = 2.0 + Math.sin(time * 1.8) * 0.5;
      } else if (state === 'speaking') {
        lightRef.current.intensity = 2.0 + Math.sin(time * 4) * 0.8;
      } else if (state === 'error') {
        lightRef.current.intensity = 1.5 + Math.random() * 0.5;
      } else {
        lightRef.current.intensity = 1.5 + pulse * 2;
      }
    }
  });

  const baseOpacity = state === 'offline' ? 0.15 : 0.65;
  const baseTrans = state === 'offline' ? 0.6 : 0.25;
  const baseRough = state === 'offline' ? 0.8 : 0.15;

  return (
    <group ref={groupRef} scale={size}>
      {/* === Outer glow halo === */}
      <mesh ref={glowRef} scale={1.35}>
        <sphereGeometry args={[1, 32, 32]} />
        <meshBasicMaterial
          ref={glowMatRef}
          color={colors.glow}
          transparent
          opacity={0.1}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* === Main core sphere === */}
      <mesh ref={coreRef}>
        <sphereGeometry args={[1, 64, 64]} />
        <meshPhysicalMaterial
          ref={coreMatRef}
          color={colors.core}
          emissive={colors.emissive}
          emissiveIntensity={0.5}
          transparent
          opacity={baseOpacity}
          roughness={baseRough}
          metalness={0.3}
          transmission={baseTrans}
          thickness={0.5}
          clearcoat={0.8}
          clearcoatRoughness={0.1}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>

      {/* === Inner glowing core === */}
      <mesh ref={innerCoreRef} scale={0.45}>
        <sphereGeometry args={[1, 32, 32]} />
        <meshBasicMaterial
          ref={innerMatRef}
          color={colors.light}
          transparent
          opacity={0.2}
          side={THREE.FrontSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* === Inner point light for self-illumination === */}
      <pointLight
        ref={lightRef}
        color={colors.light}
        intensity={1.5}
        distance={5}
        decay={2}
      />

      {/* === Secondary outer glow (larger, dimmer) === */}
      <mesh scale={1.65}>
        <sphereGeometry args={[1, 24, 24]} />
        <meshBasicMaterial
          color={colors.glow}
          transparent
          opacity={state === 'offline' ? 0.01 : 0.03}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* === Orbit Rings === */}
      <OrbitRings state={state} />

      {/* === Particle Field === */}
      {state !== 'offline' && <ParticleField state={state} />}
    </group>
  );
}
