import 'leaflet';

declare module 'leaflet' {
  interface Map {
    pm: {
      addControls: (options?: Record<string, unknown>) => void;
      /** Take the toolbar away without tearing the map down — used to make a
       *  map read-only once a job is running. */
      removeControls: () => void;
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
