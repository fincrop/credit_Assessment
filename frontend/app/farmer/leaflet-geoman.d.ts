import 'leaflet';

declare module 'leaflet' {
  interface Map {
    pm: {
      addControls: (options?: Record<string, unknown>) => void;
      enableDraw: (shape: string, options?: Record<string, unknown>) => void;
      disableDraw: () => void;
    };
  }

  interface Polygon {
    pm?: {
      enable: (options?: Record<string, unknown>) => void;
      disable: () => void;
    };
  }
}

export {};
