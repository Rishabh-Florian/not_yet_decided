"use client";
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

export default function CompanyBrain() {
  const coreRef = useRef<THREE.Mesh>(null);
  const wireRef = useRef<THREE.Mesh>(null);
  const rimRef = useRef<THREE.Mesh>(null);
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
      <ambientLight color="#dbeafe" intensity={0.45} />
      <pointLight position={[ 4,  4,  3]} color="#60a5fa" intensity={5.5} />
      <pointLight position={[-3, -2,  2]} color="#a78bfa" intensity={2.5} />
      <pointLight position={[ 0, -4, -2]} color="#1e3a8a" intensity={1.8} />

      {/* Rim glow — back-face slightly larger sphere */}
      <mesh ref={rimRef}>
        <sphereGeometry args={[1.36, 32, 32]} />
        <meshBasicMaterial
          color="#3b82f6"
          transparent
          opacity={0.07}
          side={THREE.BackSide}
        />
      </mesh>

      {/* Core sphere — indigo-navy with high metalness for visible light response */}
      <mesh ref={coreRef}>
        <icosahedronGeometry args={[1.2, 5]} />
        <meshStandardMaterial
          color="#1a3a6e"
          emissive="#0d2149"
          emissiveIntensity={0.45}
          metalness={0.88}
          roughness={0.10}
        />
      </mesh>

      {/* Wireframe cage — rotates at slightly different speed for depth */}
      <mesh ref={wireRef}>
        <icosahedronGeometry args={[1.265, 2]} />
        <meshBasicMaterial
          color="#93c5fd"
          wireframe
          transparent
          opacity={0.32}
        />
      </mesh>
    </group>
  );
}
