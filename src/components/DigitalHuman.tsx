import { useRef, useMemo } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';

// ============================================================================
// Types
// ============================================================================

type AvatarState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline';

interface DigitalHumanProps {
  state: AvatarState;
  className?: string;
  compact?: boolean;
}

// ============================================================================
// State-based color configs
// ============================================================================

interface StateColors {
  wire: string;
  wireAlt: string;
  eye: string;
  particle: string;
  bg: string;
  bloom: number;
}

const STATE_COLORS: Record<AvatarState, StateColors> = {
  idle:      { wire: '#00aaff', wireAlt: '#00ddff', eye: '#00eeff', particle: '#88ccff', bg: '#0a1628', bloom: 1.5 },
  listening: { wire: '#00ddff', wireAlt: '#44ffff', eye: '#88ffff', particle: '#aaeeff', bg: '#0c1e38', bloom: 1.8 },
  thinking:  { wire: '#8855ee', wireAlt: '#bb77ff', eye: '#9966ff', particle: '#aa77ff', bg: '#1a0a30', bloom: 1.5 },
  speaking:  { wire: '#00dddd', wireAlt: '#66ffff', eye: '#88ffff', particle: '#88ddff', bg: '#0a1828', bloom: 1.5 },
  error:     { wire: '#ff4422', wireAlt: '#ff6644', eye: '#ff3322', particle: '#ff5544', bg: '#1a0808', bloom: 1.2 },
  offline:   { wire: '#334455', wireAlt: '#445566', eye: '#334455', particle: '#445566', bg: '#080a0e', bloom: 0.3 },
};

// ============================================================================
// Create wireframe head geometry using IcosahedronGeometry with vertex displacement
// ============================================================================

function createHeadGeometry(): THREE.IcosahedronGeometry {
  const geo = new THREE.IcosahedronGeometry(1, 3);
  const pos = geo.attributes.position;

  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    let y = pos.getY(i);
    let z = pos.getZ(i);

    // 1. Overall head shape: taller, slightly narrower
    y *= 1.35;
    x *= 0.88;

    // 2. Flatten back of head
    const backFactor = Math.max(0, -z / 0.7);
    z += backFactor * 0.35;

    // 3. Jaw taper
    const jawY = Math.max(0, Math.min(1, (-y - 0.15) / 0.7));
    x *= 1.0 - jawY * 0.4;

    // 4. Chin protrusion
    const chinMask = Math.max(0, 1 - Math.abs(y + 0.7) / 0.15) * Math.max(0, 1 - Math.abs(x) / 0.2);
    z += chinMask * 0.12;

    // 5. Forehead curve
    const foreMask = Math.max(0, 1 - Math.abs(y - 0.75) / 0.2) * Math.max(0, z / 0.4);
    z += foreMask * 0.06;

    // 6. Brow ridge
    const browMask = Math.max(0, 1 - Math.abs(y - 0.3) / 0.08) * Math.max(0, 1 - Math.abs(x) / 0.25) * Math.max(0, z / 0.25);
    z += browMask * 0.06;

    // 7. Nose bridge
    const noseX = Math.max(0, 1 - Math.abs(x) / 0.06);
    const noseY = Math.max(0, Math.min(1, (y + 0.05) / 0.3)) * Math.max(0, 1 - (y - 0.05) / 0.25);
    const noseFront = Math.max(0, (z - 0.25) / 0.35);
    z += noseX * noseY * noseFront * 0.12;

    // 8. Nose tip
    const tipY = Math.max(0, 1 - Math.abs(y + 0.06) / 0.08);
    const tipFront = Math.max(0, (z - 0.4) / 0.2);
    z += tipY * tipFront * noseX * 0.08;

    // 9. Eye socket depressions
    for (const ex of [-0.22, 0.22]) {
      const dx = x - ex;
      const dy = y - 0.15;
      const dz = z - 0.52;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (dist < 0.18) {
        z -= (1 - dist / 0.18) * 0.08;
      }
    }

    // 10. Cheekbone
    const cheekY = Math.max(0, 1 - Math.abs(y) / 0.12) * Math.max(0, (Math.abs(x) - 0.22) / 0.1) * Math.max(0, 1 - (Math.abs(x) - 0.35) / 0.15);
    z += cheekY * Math.max(0, z / 0.35) * 0.05;

    // 11. Mouth lip ridge
    const mouthY = Math.max(0, 1 - Math.abs(y + 0.25) / 0.06);
    const mouthCenter = Math.max(0, 1 - Math.abs(x) / 0.12);
    z += mouthY * mouthCenter * Math.max(0, (z - 0.3) / 0.25) * 0.04;

    // 12. Ear bumps
    const earY = Math.max(0, 1 - Math.abs(y - 0.05) / 0.12);
    const earX = Math.max(0, (Math.abs(x) - 0.6) / 0.15);
    x += earY * earX * 0.08 * Math.sign(x);

    pos.setXYZ(i, x, y, z);
  }

  geo.computeVertexNormals();
  return geo;
}

// ============================================================================
// Create facial feature line geometries
// ============================================================================

function createEyeContourLine(side: number): THREE.Line {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 32; i++) {
    const t = (i / 32) * Math.PI * 2;
    const x = side * 0.22 + Math.cos(t) * 0.10;
    const y = 0.15 + Math.sin(t) * 0.042;
    const z = 0.58 + Math.cos(t) * 0.008;
    pts.push(new THREE.Vector3(x, y, z));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({
    color: 0x00ddff, transparent: true, opacity: 0.7, depthWrite: false,
  }));
}

function createNoseLine(): THREE.Line {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12;
    const y = 0.28 - t * 0.38;
    const z = 0.56 + Math.sin(t * Math.PI * 0.6) * 0.08;
    pts.push(new THREE.Vector3(0, y, z));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({
    color: 0x00ddff, transparent: true, opacity: 0.5, depthWrite: false,
  }));
}

function createJawline(): THREE.Line {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 24; i++) {
    const t = (i / 24) * Math.PI;
    const x = Math.cos(t) * 0.42;
    const y = -0.45 + Math.sin(t) * 0.18;
    const z = 0.30 + Math.cos(t) * 0.28;
    pts.push(new THREE.Vector3(x, y, z));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({
    color: 0x00aaff, transparent: true, opacity: 0.5, depthWrite: false,
  }));
}

function createBrowLine(side: number): THREE.Line {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14;
    const x = side * (0.06 + t * 0.24);
    const y = 0.30 - t * 0.05;
    const z = 0.56 + Math.sin(t * Math.PI) * 0.04;
    pts.push(new THREE.Vector3(x, y, z));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({
    color: 0x00bbff, transparent: true, opacity: 0.45, depthWrite: false,
  }));
}

function createLipLine(isUpper: boolean): THREE.Line {
  const pts: THREE.Vector3[] = [];
  const baseY = isUpper ? -0.22 : -0.26;
  const segs = 24;
  for (let i = 0; i <= segs; i++) {
    const t = i / segs;
    const x = (t - 0.5) * 2 * 0.12;
    let y = baseY;
    if (isUpper) {
      const bow = 1 - Math.exp(-Math.pow((t - 0.5) * 6, 2));
      y -= bow * 0.01;
    } else {
      y -= Math.sin(t * Math.PI) * 0.015;
    }
    const z = 0.58 - Math.pow(Math.abs(t - 0.5) * 2, 2) * 0.03;
    pts.push(new THREE.Vector3(x, y, z));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({
    color: 0x00ccff, transparent: true, opacity: 0.45, depthWrite: false,
  }));
}

// ============================================================================
// Wireframe Head component
// ============================================================================

function WireframeHead({ state }: { state: AvatarState }) {
  const groupRef = useRef<THREE.Group>(null!);
  const wireMatRef = useRef<THREE.MeshBasicMaterial>(null!);

  const headGeo = useMemo(() => createHeadGeometry(), []);

  // Pre-create all line objects
  const lines = useMemo(() => [
    createEyeContourLine(-1),
    createEyeContourLine(1),
    createNoseLine(),
    createJawline(),
    createBrowLine(-1),
    createBrowLine(1),
    createLipLine(true),
    createLipLine(false),
  ], []);

  useFrame(() => {
    const t = performance.now() * 0.001;
    const cfg = STATE_COLORS[state];

    if (wireMatRef.current) {
      wireMatRef.current.color.lerp(new THREE.Color(cfg.wire), 0.04);
    }

    // Animate lines color
    lines.forEach(line => {
      const mat = line.material as THREE.LineBasicMaterial;
      mat.color.lerp(new THREE.Color(cfg.wireAlt), 0.03);
    });

    // Head rotation
    if (groupRef.current) {
      let targetY = -0.4 + Math.sin(t * 0.12) * 0.15;
      let targetX = Math.sin(t * 0.07) * 0.04;

      if (state === 'listening') targetY += 0.15;
      else if (state === 'thinking') targetX += 0.12;
      else if (state === 'speaking') targetX += Math.sin(t * 2.5) * 0.03;
      else if (state === 'error') {
        const flicker = Math.sin(t * 12) > 0 ? 1 : 0;
        targetY += flicker * Math.sin(t * 7) * 0.1;
      }

      groupRef.current.rotation.y += (targetY - groupRef.current.rotation.y) * 0.03;
      groupRef.current.rotation.x += (targetX - groupRef.current.rotation.x) * 0.03;
    }
  });

  return (
    <group ref={groupRef} rotation={[0, -0.4, 0]}>
      {/* Main wireframe head */}
      <mesh geometry={headGeo}>
        <meshBasicMaterial
          ref={wireMatRef}
          color="#00aaff"
          wireframe
          transparent
          opacity={0.6}
          depthWrite={false}
        />
      </mesh>

      {/* Facial feature lines */}
      {lines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}
    </group>
  );
}

// ============================================================================
// Eye Glow
// ============================================================================

function EyeGlow({ state }: { state: AvatarState }) {
  const leftRef = useRef<THREE.Mesh>(null!);
  const rightRef = useRef<THREE.Mesh>(null!);
  const leftMatRef = useRef<THREE.MeshBasicMaterial>(null!);
  const rightMatRef = useRef<THREE.MeshBasicMaterial>(null!);

  const blinkTimer = useRef(0);
  const isBlinking = useRef(false);
  const nextBlink = useRef(3 + Math.random() * 2);

  useFrame((_, delta) => {
    const t = performance.now() * 0.001;
    const cfg = STATE_COLORS[state];

    blinkTimer.current += delta;
    if (!isBlinking.current && blinkTimer.current > nextBlink.current && state !== 'thinking' && state !== 'offline') {
      isBlinking.current = true;
      blinkTimer.current = 0;
    }
    if (isBlinking.current && blinkTimer.current > 0.15) {
      isBlinking.current = false;
      blinkTimer.current = 0;
      nextBlink.current = 3 + Math.random() * 2;
    }

    let scaleY = 1.0;
    if (isBlinking.current || state === 'offline') scaleY = 0.1;
    else if (state === 'thinking') scaleY = 0.3;

    [leftRef, rightRef].forEach(ref => {
      if (ref.current) ref.current.scale.y += (scaleY - ref.current.scale.y) * 0.3;
    });

    const eyeColor = new THREE.Color(cfg.eye);
    [leftMatRef, rightMatRef].forEach(ref => {
      if (ref.current) {
        ref.current.color.lerp(eyeColor, 0.05);
        if (state === 'error') {
          ref.current.opacity = Math.sin(t * 15) > 0 ? 1.0 : 0.15;
        } else if (state === 'offline') {
          ref.current.opacity = 0.05;
        } else {
          ref.current.opacity += (1.0 - ref.current.opacity) * 0.05;
        }
      }
    });
  });

  return (
    <>
      <mesh ref={leftRef} position={[-0.22, 0.15, 0.62]}>
        <sphereGeometry args={[0.035, 12, 12]} />
        <meshBasicMaterial ref={leftMatRef} color="#00eeff" transparent opacity={1} depthWrite={false} />
      </mesh>
      <mesh ref={rightRef} position={[0.22, 0.15, 0.62]}>
        <sphereGeometry args={[0.035, 12, 12]} />
        <meshBasicMaterial ref={rightMatRef} color="#00eeff" transparent opacity={1} depthWrite={false} />
      </mesh>
    </>
  );
}

// ============================================================================
// Particles
// ============================================================================

function Particles({ state, count = 120 }: { state: AvatarState; count?: number }) {
  const ref = useRef<THREE.Points>(null!);

  const { positions, seeds } = useMemo(() => {
    const pos = new Float32Array(count * 3);
    const sd = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 1.3 + Math.random() * 1.0;
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      sd[i] = Math.random() * Math.PI * 2;
    }
    return { positions: pos, seeds: sd };
  }, [count]);

  useFrame(() => {
    if (!ref.current) return;
    const posAttr = ref.current.geometry.attributes.position as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;
    const t = performance.now() * 0.001;
    const cfg = STATE_COLORS[state];

    for (let i = 0; i < count; i++) {
      const bx = positions[i * 3];
      const by = positions[i * 3 + 1];
      const bz = positions[i * 3 + 2];
      const angle = seeds[i] + t * 0.15;
      arr[i * 3] = bx * Math.cos(angle) - bz * Math.sin(angle);
      arr[i * 3 + 1] = by + Math.sin(t * 0.2 + seeds[i]) * 0.1;
      arr[i * 3 + 2] = bx * Math.sin(angle) + bz * Math.cos(angle);
    }
    posAttr.needsUpdate = true;

    // Update color
    const mat = ref.current.material as THREE.PointsMaterial;
    mat.color.lerp(new THREE.Color(cfg.particle), 0.03);
    mat.opacity = state === 'offline' ? 0.15 : 0.6;
  });

  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} count={count} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial
        size={3.0}
        color="#88ccff"
        transparent
        opacity={0.6}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

// ============================================================================
// Camera controller - slight auto movement
// ============================================================================

function CameraRig({ state }: { state: AvatarState }) {
  const { camera } = useThree();
  const target = useRef(new THREE.Vector3(0, 0.05, 2.6));

  useFrame(() => {
    const t = performance.now() * 0.001;
    const cfg = STATE_COLORS[state];
    const speed = state === 'listening' ? 0.3 : state === 'error' ? 0.5 : 0.12;

    // Subtle camera drift
    const tx = target.current.x + Math.sin(t * speed * 0.7) * 0.05;
    const ty = target.current.y + Math.sin(t * speed * 0.5) * 0.03;

    camera.position.x += (tx - camera.position.x) * 0.02;
    camera.position.y += (ty - camera.position.y) * 0.02;
    camera.lookAt(0, 0.05, 0);
  });

  return null;
}

// ============================================================================
// Scene
// ============================================================================

function Scene({ state, compact }: { state: AvatarState; compact: boolean }) {
  // Background plane color
  const bgRef = useRef<THREE.Mesh>(null!);

  useFrame(() => {
    if (bgRef.current) {
      const mat = bgRef.current.material as THREE.MeshBasicMaterial;
      mat.color.lerp(new THREE.Color(STATE_COLORS[state].bg), 0.03);
    }
  });

  return (
    <>
      <color attach="background" args={['#0a1628']} />
      <fog attach="fog" args={['#0a1628', 3, 8]} />

      <ambientLight intensity={0.15} />
      <pointLight position={[0, 1, 2]} intensity={0.5} color="#00ccff" />

      <WireframeHead state={state} />
      <EyeGlow state={state} />
      {!compact && <Particles state={state} />}
      <CameraRig state={state} />
    </>
  );
}

// ============================================================================
// Main exported component
// ============================================================================

export default function DigitalHuman({
  state = 'idle',
  className = '',
  compact = false,
}: DigitalHumanProps) {
  const size = compact ? 80 : 280;

  return (
    <div
      className={`digital-human${compact ? ' compact' : ''}${className ? ` ${className}` : ''}`}
      style={{
        width: size,
        height: size,
        position: 'relative',
        overflow: 'hidden',
        borderRadius: compact ? 8 : 16,
        background: '#0a1628',
      }}
    >
      <Canvas
        camera={{ position: [0, 0.05, 2.6], fov: 42 }}
        gl={{
          antialias: !compact,
          alpha: false,
          powerPreference: 'high-performance',
        }}
        dpr={compact ? 1 : [1, 2]}
        frameloop="always"
      >
        <Scene state={state} compact={compact} />
      </Canvas>
    </div>
  );
}
