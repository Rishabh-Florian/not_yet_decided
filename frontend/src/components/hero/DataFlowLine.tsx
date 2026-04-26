"use client";
import { useRef, useEffect } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

interface Props {
  phi: number;
  theta: number;
  radius: number;
  speed: number;
  particleSpeed: number;
  yOffset?: number;
}

function bezierPoint(t: number, p0: THREE.Vector3, p1: THREE.Vector3, p2: THREE.Vector3, p3: THREE.Vector3) {
  const mt = 1 - t;
  return new THREE.Vector3(
    mt**3*p0.x + 3*mt**2*t*p1.x + 3*mt*t**2*p2.x + t**3*p3.x,
    mt**3*p0.y + 3*mt**2*t*p1.y + 3*mt*t**2*p2.y + t**3*p3.y,
    mt**3*p0.z + 3*mt**2*t*p1.z + 3*mt*t**2*p2.z + t**3*p3.z,
  );
}

export default function DataFlowLine({ phi, theta, radius, speed, particleSpeed, yOffset = 0 }: Props) {
  const { scene } = useThree();
  const lineRef = useRef<THREE.Line | null>(null);
  const particleRef = useRef<THREE.Mesh>(null);
  const origin = new THREE.Vector3(0, 0, 0);

  useEffect(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(20 * 3), 3));
    const mat = new THREE.LineBasicMaterial({ color: "#8ea0b4", transparent: true, opacity: 0.2 });
    const line = new THREE.Line(geo, mat);
    scene.add(line);
    lineRef.current = line;
    return () => {
      scene.remove(line);
      geo.dispose();
      mat.dispose();
    };
  }, [scene]);

  useFrame(() => {
    const t = Date.now() * 0.001;
    const azimuth = phi + t * speed;
    const iconPos = new THREE.Vector3(
      radius * Math.sin(theta) * Math.cos(azimuth),
      radius * Math.cos(theta) + yOffset,
      radius * Math.sin(theta) * Math.sin(azimuth),
    );
    const mid = iconPos.clone().lerp(origin, 0.5).add(new THREE.Vector3(0, 0.4, 0));
    const cp1 = iconPos.clone().lerp(mid, 0.45);
    const cp2 = origin.clone().lerp(mid, 0.45);

    if (lineRef.current) {
      const arr = lineRef.current.geometry.attributes.position.array as Float32Array;
      for (let i = 0; i < 20; i++) {
        const p = bezierPoint(i / 19, iconPos, cp1, cp2, origin);
        arr[i * 3]     = p.x;
        arr[i * 3 + 1] = p.y;
        arr[i * 3 + 2] = p.z;
      }
      lineRef.current.geometry.attributes.position.needsUpdate = true;
      lineRef.current.geometry.computeBoundingSphere();
    }

    if (particleRef.current) {
      const progress = (t * particleSpeed) % 1;
      const pos = bezierPoint(progress, iconPos, cp1, cp2, origin);
      particleRef.current.position.copy(pos);
    }
  });

  return (
      <mesh ref={particleRef}>
      <sphereGeometry args={[0.028, 6, 6]} />
      <meshBasicMaterial color="#dbeafe" transparent opacity={0.88} />
    </mesh>
  );
}
