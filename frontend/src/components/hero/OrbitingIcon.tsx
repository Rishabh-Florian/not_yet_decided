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
  labelColor?: string;
  /** Optional: draw logo directly on canvas instead of loading SVG */
  drawLogo?: (ctx: CanvasRenderingContext2D, size: number) => void;
}

function roundRect(ctx: CanvasRenderingContext2D, size: number, r: number) {
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
}

function makeIconTexture(
  svgDataUri: string,
  label: string,
  bgColor: string,
  labelColor: string,
  drawLogo?: (ctx: CanvasRenderingContext2D, size: number) => void,
): THREE.CanvasTexture {
  const size = 256; // Higher res for crispness
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;

  function drawCard() {
    roundRect(ctx, size, 36);
    ctx.fillStyle = bgColor;
    ctx.fill();
  }

  function drawLabel() {
    const fontSize = 22;
    ctx.font = `bold ${fontSize}px system-ui, -apple-system, sans-serif`;
    ctx.fillStyle = labelColor;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, size / 2, size - 26);
  }

  drawCard();

  if (drawLogo) {
    // Direct canvas draw — no image loading needed
    ctx.save();
    roundRect(ctx, size, 36);
    ctx.clip();
    drawLogo(ctx, size);
    ctx.restore();
    drawLabel();
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;

  const img = new window.Image();
  img.onload = () => {
    drawCard();
    ctx.save();
    roundRect(ctx, size, 36);
    ctx.clip();
    const logoSize = 128;
    ctx.drawImage(img, (size - logoSize) / 2, 24, logoSize, logoSize);
    ctx.restore();
    drawLabel();
    texture.needsUpdate = true;
  };
  img.src = svgDataUri;

  return texture;
}

export default function OrbitingIcon({
  phi, theta, radius, speed, label, svgDataUri, bgColor, labelColor = "#ffffff", drawLogo,
}: Props) {
  const spriteRef = useRef<THREE.Sprite>(null);

  const material = useMemo(() => {
    const tex = makeIconTexture(svgDataUri, label, bgColor, labelColor, drawLogo);
    return new THREE.SpriteMaterial({
      map: tex,
      transparent: true,
      depthWrite: true,
      depthTest: true,
    });
  }, [svgDataUri, label, bgColor, labelColor, drawLogo]);

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

  return <sprite ref={spriteRef} material={material} scale={[0.54, 0.54, 0.54]} />;
}
