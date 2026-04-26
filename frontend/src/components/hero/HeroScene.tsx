"use client";
import { Canvas } from "@react-three/fiber";
import { Suspense } from "react";
import CompanyBrain from "./CompanyBrain";
import OrbitingIcon from "./OrbitingIcon";
import DataFlowLine from "./DataFlowLine";
import { BRAND_LOGOS } from "./brand-logos";

const INTEGRATIONS = [
  { label: "Gmail",      speed: 0.38, phi: 0.00, theta: 1.10 },
  { label: "Slack",      speed: 0.50, phi: 0.79, theta: 0.75 },
  { label: "Notion",     speed: 0.43, phi: 1.57, theta: 1.55 },
  { label: "HubSpot",    speed: 0.58, phi: 2.36, theta: 0.70 },
  { label: "GitHub",     speed: 0.40, phi: 3.14, theta: 1.80 },
  { label: "SAP",        speed: 0.64, phi: 3.93, theta: 0.90 },
  { label: "Salesforce", speed: 0.35, phi: 4.71, theta: 1.35 },
  { label: "Jira",       speed: 0.54, phi: 5.50, theta: 2.10 },
];

const RADIUS = 2.1;

export default function HeroScene() {
  return (
    <Canvas
      camera={{ position: [0, -0.4, 5.8], fov: 48 }}
      className="w-full h-full"
      gl={{ antialias: true, alpha: true, outputColorSpace: "srgb" } as never}
    >
      <Suspense fallback={null}>
        <CompanyBrain />
        {INTEGRATIONS.map((intg) => {
          const brand = BRAND_LOGOS[intg.label];
          return (
            <group key={intg.label}>
              <OrbitingIcon
                phi={intg.phi}
                theta={intg.theta}
                radius={RADIUS}
                speed={intg.speed}
                label={intg.label}
                svgDataUri={brand.svgDataUri}
                bgColor={brand.bgColor}
              />
              <DataFlowLine
                phi={intg.phi}
                theta={intg.theta}
                radius={RADIUS}
                speed={intg.speed}
                particleSpeed={intg.speed * 0.5}
              />
            </group>
          );
        })}
      </Suspense>
    </Canvas>
  );
}
