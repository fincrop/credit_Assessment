declare module 'shpjs' {
  import type { FeatureCollection } from 'geojson';

  function shp(
    base: string | ArrayBuffer | { shp: ArrayBuffer; dbf?: ArrayBuffer; prj?: string }
  ): Promise<FeatureCollection | FeatureCollection[]>;

  export default shp;
}
