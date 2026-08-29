import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Stars } from '@react-three/drei';
import {
  EffectComposer,
  Bloom,
  ChromaticAberration,
  Vignette,
} from '@react-three/postprocessing';
import { BlendFunction } from 'postprocessing';
import * as THREE from 'three';
import './JarvisAvatar3D.css';

// ============================================================================
// Types
// ============================================================================

type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline';

interface JarvisAvatar3DProps {
  state: AvatarState;
  className?: string;
  compact?: boolean;
}

// ============================================================================
// State Configuration Maps
// ============================================================================

interface StateConfig {
  coreColor: string;
  emissiveColor: string;
  glowColor: string;
  lightColor: string;
  ringColor: string;
  particleColor: string;
  faceArcColor: string;
  corePulseSpeed: number;
  corePulseAmp: number;
  ringSpeedMult: number;
  ringOpacityMult: number;
  particleSpeed: number;
  bloomIntensity: number;
}

const STATE_CONFIGS: Record<AvatarState, StateConfig> = {
  idle: {
    coreColor: '#0088cc',
    emissiveColor: '#00aaff',
    glowColor: '#00aaff',
    lightColor: '#66ccff',
    ringColor: '#3b82f6',
    particleColor: '#60a5fa',
    faceArcColor: '#88ccff',
    corePulseSpeed: 0.6,
    corePulseAmp: 0.08,
    ringSpeedMult: 1.0,
    ringOpacityMult: 1.0,
    particleSpeed: 0.5,
    bloomIntensity: 1.5,
  },
  listening: {
    coreColor: '#00aaff',
    emissiveColor: '#22ccff',
    glowColor: '#44ddff',
    lightColor: '#99eeff',
    ringColor: '#60a5fa',
    particleColor: '#93c5fd',
    faceArcColor: '#aaeeff',
    corePulseSpeed: 1.2,
    corePulseAmp: 0.12,
    ringSpeedMult: 1.6,
    ringOpacityMult: 1.5,
    particleSpeed: 1.0,
    bloomIntensity: 2.0,
  },
  thinking: {
    coreColor: '#7733cc',
    emissiveColor: '#9944ff',
    glowColor: '#8855ee',
    lightColor: '#bb99ff',
    ringColor: '#8b5cf6',
    particleColor: '#a78bfa',
    faceArcColor: '#cc99ff',
    corePulseSpeed: 2.0,
    corePulseAmp: 0.05,
    ringSpeedMult: 3.0,
    ringOpacityMult: 1.2,
    particleSpeed: 1.8,
    bloomIntensity: 1.8,
  },
  speaking: {
    coreColor: '#00bbcc',
    emissiveColor: '#00ddff',
    glowColor: '#22eeff',
    lightColor: '#77ffff',
    ringColor: '#22d3ee',
    particleColor: '#67e8f9',
    faceArcColor: '#88ffff',
    corePulseSpeed: 3.0,
    corePulseAmp: 0.15,
    ringSpeedMult: 1.2,
    ringOpacityMult: 1.3,
    particleSpeed: 0.8,
    bloomIntensity: 2.2,
  },
  error: {
    coreColor: '#cc2222',
    emissiveColor: '#ee3333',
    glowColor: '#ff4444',
    lightColor: '#ff7777',
    ringColor: '#ef4444',
    particleColor: '#f87171',
    faceArcColor: '#ff8888',
    corePulseSpeed: 5.0,
    corePulseAmp: 0.2,
    ringSpeedMult: 0.6,
    ringOpacityMult: 2.0,
    particleSpeed: 0.4,
    bloomIntensity: 1.0,
  },
  offline: {
    coreColor: '#334455',
    emissiveColor: '#445566',
    glowColor: '#556677',
    lightColor: '#778899',
    ringColor: '#64748b',
    particleColor: '#94a3b8',
    faceArcColor: '#8899aa',
    corePulseSpeed: 0.15,
    corePulseAmp: 0.02,
    ringSpeedMult: 0.2,
    ringOpacityMult: 0.3,
    particleSpeed: 0.08,
    bloomIntensity: 0.5,
  },
};

// ============================================================================
// Fresnel Glow Shader for inner transparent sphere
// ============================================================================

const fresnelVertexShader = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 worldPos = modelMatrix * vec4(position, 1.0);
    vViewDir = normalize(cameraPosition - worldPos.xyz);
    gl_Position = projectionMatrix * viewMatrix * worldPos;
  }
`;

const fresnelFragmentShader = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  void main() {
    float fresnel = pow(1.0 - abs(dot(vNormal, vViewDir)), 3.0);
    vec3 col = uColor * fresnel * uIntensity;
    gl_FragColor = vec4(col, fresnel * uOpacity);
  }
`;

// ============================================================================
// Central Energy Core
// ============================================================================

function EnergyCore({ state }: { state: AvatarState }) {
  const coreGroupRef = useRef<THREE.Group>(null!);
  const coreRef = useRef<THREE.Mesh>(null!);
  const innerRef = useRef<THREE.Mesh>(null!);
  const coreMatRef = useRef<THREE.MeshStandardMaterial>(null!);
  const innerMatRef = useRef<THREE.MeshBasicMaterial>(null!);
  const fresnelMatRef = useRef<THREE.ShaderMaterial>(null!);

  const cfg = STATE_CONFIGS[state];

  const fresnelUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color(cfg.glowColor) },
      uIntensity: { value: 2.5 },
      uOpacity: { value: 0.7 },
    }),
    [],
  );

  useFrame(() => {
    const time = performance.now() * 0.001;
    const c = STATE_CONFIGS[state];

    // Pulse calculation
    let pulse: number;
    if (state === 'error') {
      pulse = Math.sin(time * c.corePulseSpeed * Math.PI * 2) > 0
        ? Math.sin(time * c.corePulseSpeed * Math.PI * 2)
        : -0.1 + Math.random() * 0.05;
    } else {
      pulse = Math.sin(time * c.corePulseSpeed * Math.PI * 2) * c.corePulseAmp;
    }

    // Core scale breathing
    if (coreRef.current) {
      const s = 1.0 + pulse * 0.6;
      coreRef.current.scale.setScalar(s);
    }

    // Inner core (bright center)
    if (innerRef.current) {
      const s = 0.5 + pulse * 2.0;
      innerRef.current.scale.setScalar(s);
    }

    // Material color lerping
    if (coreMatRef.current) {
      coreMatRef.current.color.lerp(new THREE.Color(c.coreColor), 0.06);
      coreMatRef.current.emissive.lerp(new THREE.Color(c.emissiveColor), 0.06);
      coreMatRef.current.emissiveIntensity = 0.8 + pulse * 1.5;
    }

    if (innerMatRef.current) {
      innerMatRef.current.color.lerp(new THREE.Color(c.lightColor), 0.06);
      innerMatRef.current.opacity = state === 'offline'
        ? 0.08
        : 0.5 + pulse * 1.5;
    }

    // Fresnel glow sphere
    if (fresnelMatRef.current) {
      fresnelMatRef.current.uniforms.uColor.value.lerp(new THREE.Color(c.glowColor), 0.06);
      fresnelMatRef.current.uniforms.uOpacity.value = state === 'offline'
        ? 0.15
        : 0.6 + pulse * 0.8;
      fresnelMatRef.current.uniforms.uIntensity.value = state === 'offline'
        ? 1.0
        : 2.0 + pulse * 2.5;
    }
  });

  return (
    <group ref={coreGroupRef}>
      {/* Main core sphere */}
      <mesh ref={coreRef}>
        <sphereGeometry args={[0.15, 32, 32]} />
        <meshStandardMaterial
          ref={coreMatRef}
          color={cfg.coreColor}
          emissive={cfg.emissiveColor}
          emissiveIntensity={0.8}
          transparent
          opacity={0.85}
          roughness={0.15}
          metalness={0.4}
          toneMapped={false}
        />
      </mesh>

      {/* Inner bright point */}
      <mesh ref={innerRef} scale={0.5}>
        <sphereGeometry args={[0.15, 16, 16]} />
        <meshBasicMaterial
          ref={innerMatRef}
          color={cfg.lightColor}
          transparent
          opacity={0.5}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>

      {/* Fresnel glow outer sphere */}
      <mesh>
        <sphereGeometry args={[0.6, 32, 32]} />
        <shaderMaterial
          ref={fresnelMatRef}
          vertexShader={fresnelVertexShader}
          fragmentShader={fresnelFragmentShader}
          uniforms={fresnelUniforms}
          transparent
          side={THREE.FrontSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>

      {/* Point light */}
      <pointLight
        color={cfg.lightColor}
        intensity={state === 'offline' ? 0.5 : 3}
        distance={5}
        decay={2}
      />
    </group>
  );
}

// ============================================================================
// Concentric Orbit Rings
// ============================================================================

interface RingConfig {
  radius: number;
  segments: number;
  dotRadius: number;
  baseSpeed: number;
  direction: 1 | -1;
  tiltX: number;
  tiltZ: number;
}

const RING_CONFIGS: RingConfig[] = [
  {
    radius: 0.8,
    segments: 48,
    dotRadius: 0.008,
    baseSpeed: 0.3,
    direction: 1,
    tiltX: 0.12,
    tiltZ: 0,
  },
  {
    radius: 1.0,
    segments: 96,
    dotRadius: 0.004,
    baseSpeed: 0.5,
    direction: -1,
    tiltX: 0.6,
    tiltZ: 0.15,
  },
  {
    radius: 1.2,
    segments: 36,
    dotRadius: 0.009,
    baseSpeed: 0.8,
    direction: 1,
    tiltX: 1.2,
    tiltZ: -0.1,
  },
  {
    radius: 1.4,
    segments: 56,
    dotRadius: 0.007,
    baseSpeed: 0.2,
    direction: -1,
    tiltX: 0.35,
    tiltZ: 0.25,
  },
];

function OrbitRing({ cfg, state }: { cfg: RingConfig; state: AvatarState }) {
  const groupRef = useRef<THREE.Group>(null!);
  const meshRef = useRef<THREE.InstancedMesh>(null!);
  const matRef = useRef<THREE.MeshBasicMaterial>(null!);

  const count = cfg.segments;

  // Pre-compute instance matrices (one per dot on the ring)
  useMemo(() => {
    if (!meshRef.current) return;
    const dummy = new THREE.Object3D();
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2;
      dummy.position.set(Math.cos(angle) * cfg.radius, 0, Math.sin(angle) * cfg.radius);
      dummy.updateMatrix();
      meshRef.current.setMatrixAt(i, dummy.matrix);
    }
    meshRef.current.instanceMatrix.needsUpdate = true;
  }, [count, cfg.radius]);

  // Apply matrices after mount via useFrame (first frame)
  const matricesApplied = useRef(false);

  useFrame((_, delta) => {
    // Apply matrices on first frame if not yet done
    if (!matricesApplied.current && meshRef.current) {
      const dummy = new THREE.Object3D();
      for (let i = 0; i < count; i++) {
        const angle = (i / count) * Math.PI * 2;
        dummy.position.set(Math.cos(angle) * cfg.radius, 0, Math.sin(angle) * cfg.radius);
        dummy.updateMatrix();
        meshRef.current.setMatrixAt(i, dummy.matrix);
      }
      meshRef.current.instanceMatrix.needsUpdate = true;
      matricesApplied.current = true;
    }

    const sc = STATE_CONFIGS[state];
    const speed = cfg.baseSpeed * cfg.direction * sc.ringSpeedMult;
    groupRef.current.rotation.y += speed * delta;

    if (state === 'error') {
      const time = performance.now() * 0.001;
      groupRef.current.rotation.x = cfg.tiltX + Math.sin(time * 7 + cfg.radius * 5) * 0.06;
      groupRef.current.rotation.z = cfg.tiltZ + Math.cos(time * 5 + cfg.radius * 3) * 0.05;
    } else {
      groupRef.current.rotation.x = cfg.tiltX;
      groupRef.current.rotation.z = cfg.tiltZ;
    }

    if (matRef.current) {
      matRef.current.color.lerp(new THREE.Color(sc.ringColor), 0.06);
      matRef.current.opacity = state === 'offline'
        ? 0.12
        : 0.4 * sc.ringOpacityMult;
    }
  });

  return (
    <group ref={groupRef} rotation={[cfg.tiltX, 0, cfg.tiltZ]}>
      <instancedMesh
        ref={meshRef}
        args={[undefined, undefined, count]}
        frustumCulled={false}
      >
        <sphereGeometry args={[cfg.dotRadius, 6, 6]} />
        <meshBasicMaterial
          ref={matRef}
          color={STATE_CONFIGS[state].ringColor}
          transparent
          opacity={0.4}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </instancedMesh>
    </group>
  );
}

// ============================================================================
// Face-like Arc Features (eyebrows + mouth)
// ============================================================================

function FaceArcs({ state }: { state: AvatarState }) {
  const leftEyeRef = useRef<THREE.Group>(null!);
  const rightEyeRef = useRef<THREE.Group>(null!);
  const mouthRef = useRef<THREE.Group>(null!);
  const leftMatRef = useRef<THREE.MeshBasicMaterial>(null!);
  const rightMatRef = useRef<THREE.MeshBasicMaterial>(null!);
  const mouthMatRef = useRef<THREE.MeshBasicMaterial>(null!);

  const arcRadius = 0.32;
  const tubeRadius = 0.006;

  useFrame(() => {
    const time = performance.now() * 0.001;
    const sc = STATE_CONFIGS[state];

    // Eye arcs brightness
    const eyeBrightness = state === 'listening' ? 1.5 : state === 'offline' ? 0.25 : 1.0;

    if (leftMatRef.current) {
      leftMatRef.current.color.lerp(new THREE.Color(sc.faceArcColor), 0.06);
      leftMatRef.current.opacity = state === 'offline'
        ? 0.08
        : 0.35 * eyeBrightness;
    }
    if (rightMatRef.current) {
      rightMatRef.current.color.lerp(new THREE.Color(sc.faceArcColor), 0.06);
      rightMatRef.current.opacity = state === 'offline'
        ? 0.08
        : 0.35 * eyeBrightness;
    }

    // Mouth arc animation
    if (mouthMatRef.current) {
      mouthMatRef.current.color.lerp(new THREE.Color(sc.faceArcColor), 0.06);
      let mouthOpacity = state === 'offline' ? 0.06 : 0.25;

      if (state === 'speaking') {
        // Animate mouth arc: pulse scale and rotate slightly
        const speakPulse = 0.8 + Math.sin(time * 8) * 0.3 + Math.sin(time * 12) * 0.15;
        mouthRef.current.scale.set(speakPulse, 1, 1);
        mouthRef.current.rotation.z = Math.sin(time * 6) * 0.05;
        mouthOpacity = 0.3 + Math.sin(time * 10) * 0.15;
      } else if (state === 'error') {
        mouthRef.current.scale.set(1 + Math.random() * 0.1, 1, 1);
        mouthOpacity = 0.15 + Math.random() * 0.2;
      } else {
        mouthRef.current.scale.set(1, 1, 1);
        mouthRef.current.rotation.z *= 0.95;
      }

      mouthMatRef.current.opacity = mouthOpacity;
    }

    // Listening: eye arcs brighten + slight movement
    if (state === 'listening') {
      leftEyeRef.current.position.y = 0.22 + Math.sin(time * 2) * 0.005;
      rightEyeRef.current.position.y = 0.22 + Math.sin(time * 2 + 0.5) * 0.005;
    } else {
      leftEyeRef.current.position.y = 0.22;
      rightEyeRef.current.position.y = 0.22;
    }
  });

  return (
    <group>
      {/* Left eye arc (brow) */}
      <group ref={leftEyeRef} position={[-0.12, 0.22, 0]} rotation={[0, 0, -0.15]}>
        <mesh>
          <torusGeometry args={[arcRadius, tubeRadius, 8, 32, Math.PI * 0.4]} />
          <meshBasicMaterial
            ref={leftMatRef}
            color={STATE_CONFIGS[state].faceArcColor}
            transparent
            opacity={0.35}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            toneMapped={false}
          />
        </mesh>
      </group>

      {/* Right eye arc (brow) */}
      <group ref={rightEyeRef} position={[0.12, 0.22, 0]} rotation={[0, 0, Math.PI + 0.15]}>
        <mesh>
          <torusGeometry args={[arcRadius, tubeRadius, 8, 32, Math.PI * 0.4]} />
          <meshBasicMaterial
            ref={rightMatRef}
            color={STATE_CONFIGS[state].faceArcColor}
            transparent
            opacity={0.35}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            toneMapped={false}
          />
        </mesh>
      </group>

      {/* Mouth arc (subtle smile) */}
      <group ref={mouthRef} position={[0, -0.25, 0]} rotation={[0, 0, Math.PI]}>
        <mesh>
          <torusGeometry args={[arcRadius * 0.7, tubeRadius * 0.8, 8, 24, Math.PI * 0.3]} />
          <meshBasicMaterial
            ref={mouthMatRef}
            color={STATE_CONFIGS[state].faceArcColor}
            transparent
            opacity={0.25}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            toneMapped={false}
          />
        </mesh>
      </group>
    </group>
  );
}

// ============================================================================
// Particle Field (instanced spheres for high-end look)
// ============================================================================

const PARTICLE_COUNT_FULL = 200;
const PARTICLE_COUNT_COMPACT = 40;

function ParticleField({
  state,
  compact = false,
}: {
  state: AvatarState;
  compact?: boolean;
}) {
  const pointsRef = useRef<THREE.Points>(null!);
  const count = compact ? PARTICLE_COUNT_COMPACT : PARTICLE_COUNT_FULL;

  const { basePositions, velocities, seeds } = useMemo(() => {
    const base = new Float32Array(count * 3);
    const vel = new Float32Array(count);
    const sd = new Float32Array(count);

    for (let i = 0; i < count; i++) {
      const phi = Math.acos(1 - (2 * (i + 0.5)) / count);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      const r = 1.5 + Math.random() * 0.5;

      base[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      base[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      base[i * 3 + 2] = r * Math.cos(phi);

      vel[i] = 0.2 + Math.random() * 0.8;
      sd[i] = Math.random() * Math.PI * 2;
    }

    return { basePositions: base, velocities: vel, seeds: sd };
  }, [count]);

  const positions = useMemo(() => new Float32Array(count * 3), [count]);
  const colorArray = useMemo(() => new Float32Array(count * 3), [count]);

  useFrame(() => {
    if (!pointsRef.current) return;
    const geo = pointsRef.current.geometry;
    const posAttr = geo.getAttribute('position') as THREE.BufferAttribute;
    const colAttr = geo.getAttribute('color') as THREE.BufferAttribute;
    const posArr = posAttr.array as Float32Array;
    const colArr = colAttr.array as Float32Array;

    const sc = STATE_CONFIGS[state];
    const speed = sc.particleSpeed;
    const targetColor = new THREE.Color(sc.particleColor);
    const time = performance.now() * 0.001;

    for (let i = 0; i < count; i++) {
      const bx = basePositions[i * 3];
      const by = basePositions[i * 3 + 1];
      const bz = basePositions[i * 3 + 2];

      const angle = seeds[i] + time * speed * velocities[i] * 0.3;
      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);

      let nx = bx * cosA - bz * sinA;
      let ny = by;
      let nz = bx * sinA + bz * cosA;

      // Speaking: pulse outward
      if (state === 'speaking') {
        const p = Math.sin(time * 6 + seeds[i]) * 0.25;
        const dist = Math.sqrt(nx * nx + ny * ny + nz * nz);
        const invDist = dist > 0.001 ? 1 / dist : 0;
        nx += nx * invDist * p;
        ny += ny * invDist * p;
        nz += nz * invDist * p;
      }

      // Error: erratic jitter
      if (state === 'error') {
        nx += Math.sin(time * 15 + seeds[i] * 10) * 0.12;
        ny += Math.cos(time * 12 + seeds[i] * 7) * 0.1;
        nz += Math.sin(time * 18 + seeds[i] * 5) * 0.1;
      }

      // Offline: pull inward
      if (state === 'offline') {
        nx *= 0.65;
        ny *= 0.65;
        nz *= 0.65;
      }

      posArr[i * 3] = nx;
      posArr[i * 3 + 1] = ny;
      posArr[i * 3 + 2] = nz;

      colArr[i * 3] = targetColor.r;
      colArr[i * 3 + 1] = targetColor.g;
      colArr[i * 3 + 2] = targetColor.b;
    }

    posAttr.needsUpdate = true;
    colAttr.needsUpdate = true;
  });

  const size = compact ? 0.025 : 0.035;

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          args={[positions, 3]}
          count={count}
          itemSize={3}
        />
        <bufferAttribute
          attach="attributes-color"
          args={[colorArray, 3]}
          count={count}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={size}
        vertexColors
        transparent
        opacity={state === 'offline' ? 0.15 : 0.85}
        sizeAttenuation
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </points>
  );
}

// ============================================================================
// Post-processing Effects
// ============================================================================

function PostProcessingEffects({ state }: { state: AvatarState }) {
  const bloomIntensity = STATE_CONFIGS[state].bloomIntensity;
  const chromaticOffset = useMemo(() => new THREE.Vector2(0.002, 0.002), []);

  return (
    <EffectComposer multisampling={0}>
      <Bloom
        intensity={bloomIntensity}
        luminanceThreshold={0.15}
        luminanceSmoothing={0.9}
        mipmapBlur
        radius={0.85}
      />
      <ChromaticAberration
        blendFunction={BlendFunction.NORMAL}
        offset={chromaticOffset}
      />
      <Vignette
        offset={0.3}
        darkness={0.7}
        blendFunction={BlendFunction.NORMAL}
      />
    </EffectComposer>
  );
}

// ============================================================================
// Inner Scene Content
// ============================================================================

function SceneContent({ state, compact }: { state: AvatarState; compact: boolean }) {
  return (
    <>
      {/* Background */}
      <color attach="background" args={['#050510']} />

      {/* Ambient for subtle depth */}
      <ambientLight intensity={0.05} />

      {/* Energy core */}
      <EnergyCore state={state} />

      {/* Face-like arcs */}
      <FaceArcs state={state} />

      {/* Concentric rings */}
      {RING_CONFIGS.map((cfg, i) => (
        <OrbitRing key={i} cfg={cfg} state={state} />
      ))}

      {/* Particle field */}
      <ParticleField state={state} compact={compact} />

      {/* Far background stars */}
      <Stars
        radius={30}
        depth={40}
        count={compact ? 200 : 600}
        factor={2}
        saturation={0.2}
        fade
        speed={0.3}
      />

      {/* Post-processing (skip for compact) */}
      {!compact && <PostProcessingEffects state={state} />}
    </>
  );
}

// ============================================================================
// Main Exported Component
// ============================================================================

export default function JarvisAvatar3D({
  state = 'idle',
  className = '',
  compact = false,
}: JarvisAvatar3DProps) {
  const containerClass = `jarvis-avatar-3d${compact ? ' compact' : ''}${className ? ` ${className}` : ''}`;

  return (
    <div className={containerClass}>
      <Canvas
        camera={{ position: [0, 0, 3.2], fov: 50 }}
        gl={{
          antialias: !compact,
          alpha: false,
          powerPreference: 'high-performance',
          toneMapping: THREE.ACESFilmicToneMapping,
          toneMappingExposure: 1.0,
        }}
        dpr={compact ? 1 : [1, 2]}
        frameloop="always"
      >
        <SceneContent state={state} compact={compact} />
      </Canvas>
    </div>
  );
}
