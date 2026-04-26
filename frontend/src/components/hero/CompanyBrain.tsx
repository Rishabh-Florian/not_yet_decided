"use client";
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

export default function CompanyBrain() {
  const coreRef = useRef<THREE.Mesh>(null);
  const wireRef = useRef<THREE.Mesh>(null);
  const rimRef = useRef<THREE.Mesh>(null);
  const pointsRef = useRef<THREE.Group>(null);
  const isHovered = useRef(false);

  useFrame((_, delta) => {
    const speed = isHovered.current ? delta * 0.30 : delta * 0.07;
    if (coreRef.current) {
      coreRef.current.rotation.y += speed;
      coreRef.current.rotation.x += speed * 0.22;
    }
    if (wireRef.current) {
      wireRef.current.rotation.y += speed * 1.12;
      wireRef.current.rotation.x -= speed * 0.16;
    }
    if (pointsRef.current) {
      pointsRef.current.rotation.y += speed * 1.12;
      pointsRef.current.rotation.x -= speed * 0.16;
    }
    if (rimRef.current) {
      const pulse = 1 + Math.sin(Date.now() * 0.0012) * 0.018;
      rimRef.current.scale.setScalar(pulse);
    }
  });

  return (
    <group
      onPointerOver={() => { isHovered.current = true; }}
      onPointerOut={() => { isHovered.current = false; }}
    >
      {/* Three-point lighting */}
      <ambientLight color="#d4d4d8" intensity={0.32} />
      <pointLight position={[4, 4, 3]} color="#f1f5f9" intensity={2.2} />
      <pointLight position={[-3, -2, 2]} color="#c9ced6" intensity={1.4} />
      <pointLight position={[0, -4, -2]} color="#4b5563" intensity={0.85} />

      {/* Rim glow — back-face slightly larger sphere */}
      <mesh ref={rimRef}>
        <sphereGeometry args={[1.42, 32, 32]} />
        <meshBasicMaterial
          color="#60a5fa"
          transparent
          opacity={0.08}
          side={THREE.BackSide}
        />
      </mesh>

      {/* Core sphere */}
      <mesh ref={coreRef}>
        <icosahedronGeometry args={[1.28, 5]} />
        <meshStandardMaterial
          color="#0a0a0a"
          emissive="#0f1115"
          emissiveIntensity={0.22}
          metalness={0.82}
          roughness={0.18}
        />
      </mesh>

      {/* Wireframe cage — rotates at slightly different speed for depth */}
      <mesh ref={wireRef}>
        <icosahedronGeometry args={[1.34, 2]} />
        <meshBasicMaterial
          color="#c0c5cc"
          wireframe
          transparent
          opacity={0.42}
        />
      </mesh>

      {/* Highlighted graph points */}
      <group ref={pointsRef}>
        <mesh position={[0, 1.34, 0.02]}>
          <sphereGeometry args={[0.03, 10, 10]} />
          <meshBasicMaterial color="#f8fafc" />
        </mesh>
        <mesh position={[-1.08, -0.44, 0.58]}>
          <sphereGeometry args={[0.028, 10, 10]} />
          <meshBasicMaterial color="#dbeafe" />
        </mesh>
        <mesh position={[0.94, 0.24, -0.95]}>
          <sphereGeometry args={[0.028, 10, 10]} />
          <meshBasicMaterial color="#e2e8f0" />
        </mesh>
      </group>
    </group>
  );
}
