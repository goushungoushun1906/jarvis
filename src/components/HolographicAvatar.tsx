import { useRef, useMemo, useCallback } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

export type AvatarState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "error"
  | "offline";

const STATE_COLORS: Record<AvatarState, { h: string; g: string; e: string }> = {
  idle: { h: "#3b82f6", g: "#60a5fa", e: "#1d4ed8" },
  listening: { h: "#60a5fa", g: "#93c5fd", e: "#3b82f6" },
  thinking: { h: "#8b5cf6", g: "#a78bfa", e: "#6d28d9" },
  speaking: { h: "#22d3ee", g: "#67e8f9", e: "#0891b2" },
  error: { h: "#ef4444", g: "#f87171", e: "#b91c1c" },
  offline: { h: "#475569", g: "#64748b", e: "#334155" },
};

/* ================================================================
   Wireframe Head — built from icosphere geometry
   ================================================================ */
function WireframeHead({
  state,
  mouthOpen,
}: {
  state: AvatarState;
  mouthOpen: number; // 0‑1
}) {
  const headRef = useRef<THREE.Mesh>(null!);
  const wireMatRef = useRef<THREE.ShaderMaterial>(null!);
  const jawRef = useRef<THREE.Mesh>(null!);
  const jawMatRef = useRef<THREE.ShaderMaterial>(null!);

  // vertex shader for holographic scan-line
  const vertShader = `
    varying vec3 vPos;
    varying vec3 vNorm;
    void main(){
      vPos = position;
      vNorm = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
    }
  `;

  // fragment shader with scan-line + Fresnel glow
  const fragShader = `
    uniform vec3 uColor;
    uniform float uTime;
    uniform float uOpacity;
    uniform float uFlicker;
    varying vec3 vPos;
    varying vec3 vNorm;
    void main(){
      float scan = sin(vPos.y * 30.0 - uTime * 3.0) * 0.5 + 0.5;
      scan = smoothstep(0.3, 0.7, scan) * 0.35;
      float fresnel = pow(1.0 - abs(dot(vNorm, vec3(0.0,0.0,1.0))), 2.5);
      float alpha = (0.25 + fresnel * 0.6 + scan) * uOpacity * uFlicker;
      vec3 col = uColor + vec3(fresnel * 0.15);
      gl_FragColor = vec4(col, alpha);
    }
  `;

  const sc = useMemo(() => STATE_COLORS[state], [state]);

  const uniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color(sc.h) },
      uTime: { value: 0 },
      uOpacity: { value: state === "offline" ? 0.08 : 0.85 },
      uFlicker: { value: 1.0 },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sc.h]
  );

  useFrame((_, delta) => {
    const t = performance.now() * 0.001;
    const curSc = STATE_COLORS[state];

    // lerp color
    const target = new THREE.Color(curSc.h);
    uniforms.uColor.value.lerp(target, 0.04);
    uniforms.uTime.value = t;
    uniforms.uOpacity.value +=
      ((state === "offline" ? 0.08 : 0.85) - uniforms.uOpacity.value) * 0.05;

    // error flicker
    uniforms.uFlicker.value =
      state === "error" ? (Math.sin(t * 20) > 0 ? 1.0 : 0.15) : 1.0;

    // head subtle rotation
    if (headRef.current) {
      const speed =
        state === "thinking"
          ? 0.25
          : state === "listening"
          ? 0.12
          : state === "idle"
          ? 0.04
          : 0.0;
      headRef.current.rotation.y += speed * delta;
      // subtle nod
      headRef.current.rotation.x =
        Math.sin(t * (state === "listening" ? 1.5 : 0.5)) * 0.03;
    }

    // jaw open for speaking
    if (jawRef.current) {
      const targetY = -0.38 + mouthOpen * 0.12;
      jawRef.current.position.y += (targetY - jawRef.current.position.y) * 0.3;
    }
  });

  // upper head geometry (sphere, slightly stretched vertically)
  const upperGeo = useMemo(() => {
    const g = new THREE.IcosahedronGeometry(1, 3);
    const pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      let y = pos.getY(i);
      // squash below y=-0.3 (jaw area) for the upper head
      if (y < -0.3) {
        y = -0.3 + (y + 1) * 0.05;
        pos.setY(i, y);
      }
      // flatten back slightly for profile
      let z = pos.getZ(i);
      z *= 0.85;
      pos.setZ(i, z);
    }
    g.computeVertexNormals();
    return g;
  }, []);

  // jaw / lower face geometry
  const jawGeo = useMemo(() => {
    const g = new THREE.IcosahedronGeometry(1, 3);
    const pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      let y = pos.getY(i);
      // keep only below -0.2
      if (y > -0.2) {
        y = -0.2 - (1.0 - y) * 0.3;
        pos.setY(i, y);
      }
      let z = pos.getZ(i);
      z *= 0.85;
      pos.setZ(i, z);
    }
    g.computeVertexNormals();
    return g;
  }, []);

  return (
    <group>
      {/* Upper head wireframe */}
      <mesh ref={headRef} geometry={upperGeo}>
        <shaderMaterial
          ref={wireMatRef}
          vertexShader={vertShader}
          fragmentShader={fragShader}
          uniforms={uniforms}
          transparent
          wireframe
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Jaw — moves down when speaking */}
      <mesh ref={jawRef} position={[0, -0.38, 0]} geometry={jawGeo}>
        <shaderMaterial
          ref={jawMatRef}
          vertexShader={vertShader}
          fragmentShader={fragShader}
          uniforms={uniforms}
          transparent
          wireframe
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  );
}

/* ================================================================
   Eyes — two glowing ellipsoids
   ================================================================ */
function Eyes({ state }: { state: AvatarState }) {
  const lRef = useRef<THREE.Mesh>(null!);
  const rRef = useRef<THREE.Mesh>(null!);
  const matRef = useRef<THREE.MeshBasicMaterial>(null!);

  const sc = useMemo(() => STATE_COLORS[state], [state]);

  useFrame(() => {
    const t = performance.now() * 0.001;
    const curSc = STATE_COLORS[state];
    const col = new THREE.Color(curSc.g);
    if (matRef.current) {
      matRef.current.color.lerp(col, 0.04);
      const base =
        state === "thinking"
          ? 0.9
          : state === "speaking"
          ? 0.7
          : state === "offline"
          ? 0.05
          : 0.55;
      matRef.current.opacity = base + Math.sin(t * 1.5) * 0.1;
    }
    // blink every 3-5s
    const blink = Math.sin(t * 0.3) > 0.97;
    const sy = blink ? 0.15 : 1;
    if (lRef.current) lRef.current.scale.y += (sy - lRef.current.scale.y) * 0.3;
    if (rRef.current) rRef.current.scale.y += (sy - rRef.current.scale.y) * 0.3;
  });

  return (
    <>
      <mesh ref={lRef} position={[-0.28, 0.22, 0.78]}>
        <sphereGeometry args={[0.08, 16, 16]} />
        <meshBasicMaterial
          ref={matRef}
          color={sc.g}
          transparent
          opacity={0.6}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh ref={rRef} position={[0.28, 0.22, 0.78]}>
        <sphereGeometry args={[0.08, 16, 16]} />
        <meshBasicMaterial
          color={sc.g}
          transparent
          opacity={0.6}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </>
  );
}

/* ================================================================
   Sound Waveform Ring — around mouth when speaking
   ================================================================ */
function MouthWaveform({
  state,
  amplitude,
}: {
  state: AvatarState;
  amplitude: number; // 0‑1
}) {
  const ref = useRef<THREE.Line>(null!);
  const matRef = useRef<THREE.LineBasicMaterial>(null!);

  const MAX_POINTS = 64;

  const [geo] = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(MAX_POINTS * 3);
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return [g];
  }, []);

  useFrame(() => {
    if (!ref.current) return;
    const t = performance.now() * 0.001;
    const curSc = STATE_COLORS[state];
    const col = new THREE.Color(curSc.g);
    matRef.current.color.lerp(col, 0.04);

    const posAttr = geo.attributes.position as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;
    const mouthY = -0.35;
    const mouthZ = 0.82;
    const radius = 0.18;

    for (let i = 0; i < MAX_POINTS; i++) {
      const angle = (i / MAX_POINTS) * Math.PI * 2;
      // only show front half (mouth)
      const x = Math.cos(angle) * radius;
      const y = mouthY + Math.sin(angle) * 0.06;
      // z displacement = waveform
      const wave =
        state === "speaking"
          ? Math.sin(angle * 8 + t * 12) * amplitude * 0.08
          : 0;
      const z = mouthZ + Math.sin(angle) * 0.04 + wave;
      arr[i * 3] = x;
      arr[i * 3 + 1] = y;
      arr[i * 3 + 2] = Math.max(0, z);
    }
    posAttr.needsUpdate = true;

    matRef.current.opacity =
      state === "speaking" ? 0.4 + amplitude * 0.4 : 0.05;
  });

  return (
    // @ts-expect-error R3F <line> JSX type conflicts with SVGLineElement
    <line ref={ref as any} geometry={geo}>
      <lineBasicMaterial
        ref={matRef}
        color="#22d3ee"
        transparent
        opacity={0.05}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </line>
  );
}

/* ================================================================
   Floating Particles around head
   ================================================================ */
function HoloParticles({ state }: { state: AvatarState }) {
  const ref = useRef<THREE.Points>(null!);
  const COUNT = 200;

  const [geo, velocities] = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(COUNT * 3);
    const vel = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 1.4 + Math.random() * 0.8;
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = r * Math.cos(phi);
      vel[i * 3] = (Math.random() - 0.5) * 0.002;
      vel[i * 3 + 1] = (Math.random() - 0.5) * 0.002;
      vel[i * 3 + 2] = (Math.random() - 0.5) * 0.002;
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return [g, vel];
  }, []);

  useFrame(() => {
    if (!ref.current || state === "offline") return;
    const posAttr = geo.attributes.position as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;
    for (let i = 0; i < COUNT; i++) {
      arr[i * 3] += velocities[i * 3];
      arr[i * 3 + 1] += velocities[i * 3 + 1];
      arr[i * 3 + 2] += velocities[i * 3 + 2];
      // keep within sphere
      const x = arr[i * 3],
        y = arr[i * 3 + 1],
        z = arr[i * 3 + 2];
      const dist = Math.sqrt(x * x + y * y + z * z);
      if (dist > 2.5 || dist < 1.3) {
        velocities[i * 3] *= -1;
        velocities[i * 3 + 1] *= -1;
        velocities[i * 3 + 2] *= -1;
      }
    }
    posAttr.needsUpdate = true;
  });

  const sc = useMemo(() => STATE_COLORS[state], [state]);

  return (
    <points ref={ref} geometry={geo}>
      <pointsMaterial
        color={sc.g}
        size={0.02}
        transparent
        opacity={state === "offline" ? 0 : 0.6}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

/* ================================================================
   Horizontal orbit rings
   ================================================================ */
function OrbitRings({ state }: { state: AvatarState }) {
  const r1 = useRef<THREE.Mesh>(null!);
  const r2 = useRef<THREE.Mesh>(null!);

  const sc = useMemo(() => STATE_COLORS[state], [state]);

  useFrame((_, delta) => {
    const speed =
      state === "thinking" ? 0.8 : state === "speaking" ? 0.5 : 0.15;
    if (r1.current) r1.current.rotation.z += speed * delta;
    if (r2.current) r2.current.rotation.z -= speed * 0.7 * delta;
  });

  return (
    <>
      <mesh ref={r1} rotation={[0.3, 0, 0]}>
        <torusGeometry args={[1.3, 0.005, 8, 80]} />
        <meshBasicMaterial
          color={sc.g}
          transparent
          opacity={state === "offline" ? 0.03 : 0.25}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh ref={r2} rotation={[-0.5, 0.4, 0]}>
        <torusGeometry args={[1.5, 0.004, 8, 80]} />
        <meshBasicMaterial
          color={sc.h}
          transparent
          opacity={state === "offline" ? 0.02 : 0.15}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </>
  );
}

/* ================================================================
   Inner scene controller — ties mouth to speaking state
   ================================================================ */
function HologramScene({
  state,
}: {
  state: AvatarState;
}) {
  const mouthOpenRef = useRef(0);
  const waveformAmpRef = useRef(0);
  const shapeIdxRef = useRef(0);
  const frameIdRef = useRef(0);

  useFrame(() => {
    const t = performance.now() * 0.001;

    if (state === "speaking") {
      // cycle through mouth shapes
      const shapes = [0.2, 0.6, 1.0, 0.7, 0.3, 0.8, 1.0, 0.5, 0.1, 0.9];
      shapeIdxRef.current = (shapeIdxRef.current + 1) % shapes.length;
      const target = shapes[shapeIdxRef.current];
      mouthOpenRef.current += (target - mouthOpenRef.current) * 0.25;
      waveformAmpRef.current += (1.0 - waveformAmpRef.current) * 0.15;
    } else {
      mouthOpenRef.current *= 0.85; // close mouth smoothly
      waveformAmpRef.current *= 0.9;
    }
  });

  return (
    <>
      <ambientLight intensity={0.1} />
      <WireframeHead
        state={state}
        mouthOpen={mouthOpenRef.current}
      />
      <Eyes state={state} />
      <MouthWaveform
        state={state}
        amplitude={waveformAmpRef.current}
      />
      {state !== "offline" && <HoloParticles state={state} />}
      <OrbitRings state={state} />

      {/* Outer glow sphere */}
      <mesh scale={1.8}>
        <sphereGeometry args={[1, 16, 16]} />
        <meshBasicMaterial
          color={STATE_COLORS[state].h}
          transparent
          opacity={state === "offline" ? 0.01 : 0.04}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      <pointLight
        color={STATE_COLORS[state].g}
        intensity={state === "offline" ? 0.2 : 1.5}
        distance={6}
        decay={2}
      />
    </>
  );
}

/* ================================================================
   Public component
   ================================================================ */
interface HolographicAvatarProps {
  state: AvatarState;
  emotion?: string;
  className?: string;
  speakingText?: string;
  compact?: boolean;
}

export default function HolographicAvatar({
  state,
  emotion,
  className,
  compact = false,
}: HolographicAvatarProps) {
  const containerStyle: React.CSSProperties = compact
    ? { width: 80, height: 80 }
    : { width: 280, height: 280 };

  return (
    <div className={`holographic-avatar ${className || ""}`} data-state={state}>
      <div style={containerStyle}>
        <Canvas
          camera={{ position: [0, 0, 3.5], fov: 45 }}
          gl={{
            antialias: true,
            alpha: true,
            powerPreference: "high-performance",
          }}
          dpr={[1, 1.5]}
        >
          <HologramScene state={state} />
        </Canvas>
      </div>
    </div>
  );
}
