"use client";
import { useRef, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

interface Props {
  phi: number;
  theta: number;
  radius: number;
  speed: number;
  label: string;
  svgDataUri: string;
  bgColor: string;
}

function makeIconTexture(svgDataUri: string, label: string, bgColor: string): THREE.CanvasTexture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;

  // Rounded rect clip
  const r = 22;
  ctx.beginPath();
  ctx.moveTo(r, 0);
  ctx.lineTo(size - r, 0);
  ctx.quadraticCurveTo(size, 0, size, r);
  ctx.lineTo(size, size - r);
  ctx.quadraticCurveTo(size, size, size - r, size);
  ctx.lineTo(r, size);
  ctx.quadraticCurveTo(0, size, 0, size - r);
  ctx.lineTo(0, r);
  ctx.quadraticCurveTo(0, 0, r, 0);
  ctx.closePath();
  ctx.fillStyle = bgColor;
  ctx.fill();

  // Logo image
  const img = new window.Image();
  img.onload = () => {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.lineTo(size - r, 0);
    ctx.quadraticCurveTo(size, 0, size, r);
    ctx.lineTo(size, size - r);
    ctx.quadraticCurveTo(size, size, size - r, size);
    ctx.lineTo(r, size);
    ctx.quadraticCurveTo(0, size, 0, size - r);
    ctx.lineTo(0, r);
    ctx.quadraticCurveTo(0, 0, r, 0);
    ctx.closePath();
    ctx.clip();
    // Center logo, leave room for label at bottom
    const logoSize = 62;
    const logoX = (size - logoSize) / 2;
    const logoY = 18;
    ctx.drawImage(img, logoX, logoY, logoSize, logoSize);
    ctx.restore();

    // Label
    ctx.font = "bold 14px system-ui, -apple-system, sans-serif";
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, size / 2, size - 17);

    texture.needsUpdate = true;
  };
  img.src = svgDataUri;

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export default function OrbitingIcon({ phi, theta, radius, speed, label, svgDataUri, bgColor }: Props) {
  const spriteRef = useRef<THREE.Sprite>(null);

  const material = useMemo(() => {
    const tex = makeIconTexture(svgDataUri, label, bgColor);
    return new THREE.SpriteMaterial({
      map: tex,
      transparent: true,
      depthWrite: true,
      depthTest: true,
    });
  }, [svgDataUri, label, bgColor]);

  useFrame(() => {
    if (!spriteRef.current) return;
    const t = Date.now() * 0.001;
    const azimuth = phi + t * speed;
    spriteRef.current.position.set(
      radius * Math.sin(theta) * Math.cos(azimuth),
      radius * Math.cos(theta),
      radius * Math.sin(theta) * Math.sin(azimuth),
    );
  });

  return <sprite ref={spriteRef} material={material} scale={[0.55, 0.55, 0.55]} />;
}
