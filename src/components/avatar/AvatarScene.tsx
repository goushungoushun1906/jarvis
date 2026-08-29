import { Canvas } from '@react-three/fiber';
import JarvisCore from './JarvisCore';

interface AvatarSceneProps {
  state: string;
  emotion?: string;
  className?: string;
}

export default function AvatarScene({ state, emotion = 'neutral', className }: AvatarSceneProps) {
  return (
    <div className={className} style={{ width: '100%', height: '100%' }}>
      <Canvas
        camera={{ position: [0, 0, 5], fov: 50 }}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance',
        }}
        dpr={[1, 2]}
      >
        {/* Minimal ambient for depth perception */}
        <ambientLight intensity={0.15} />

        {/* Subtle rim light from behind */}
        <directionalLight position={[0, 0, -3]} intensity={0.1} color="#ffffff" />

        {/* The JARVIS core */}
        <JarvisCore
          state={state as 'idle' | 'listening' | 'thinking' | 'speaking' | 'error' | 'offline'}
          emotion={emotion as 'happy' | 'focused' | 'confused' | 'neutral'}
        />
      </Canvas>
    </div>
  );
}
