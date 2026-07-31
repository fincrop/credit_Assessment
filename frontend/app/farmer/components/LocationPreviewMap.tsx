'use client';

import dynamic from 'next/dynamic';

const Inner = dynamic(() => import('./LocationPreviewMapInner'), {
  ssr: false,
  loading: () => (
    <div className="farm-map-container flex items-center justify-center text-sm text-stone-500" style={{ minHeight: 360 }}>
      Loading map…
    </div>
  ),
});

export function LocationPreviewMap(props: {
  center?: { lat: number; lng: number } | null;
  label?: string;
}) {
  return <Inner {...props} />;
}
