/**
 * SBI General Insurance kharif clusters. Each district has two revenue circles
 * per crop, and the cluster page compares that pair.
 */

export interface RevenueCircle {
  id: string;
  name: string;
  district: 'Latur' | 'Beed';
  taluka: string;
  crop: 'Cotton' | 'Soybean';
  /** Class name the fused model emits. */
  modelCrop: 'Cotton' | 'Soyabean';
  villages: number;
  areaHa: number;
  sowing: string;
}

export interface ClusterPair {
  id: string;
  district: 'Latur' | 'Beed';
  crop: 'Cotton' | 'Soybean';
  modelCrop: 'Cotton' | 'Soyabean';
  sowing: string;
  rcIds: [string, string];
}

export const REVENUE_CIRCLES: RevenueCircle[] = [
  { id: 'khandali', name: 'Khandali', district: 'Latur', taluka: 'Ahmadpur', crop: 'Cotton', modelCrop: 'Cotton', villages: 17, areaHa: 1037, sowing: 'Onset of monsoon to 15 July' },
  { id: 'hadolati', name: 'Hadolati', district: 'Latur', taluka: 'Ahmadpur', crop: 'Cotton', modelCrop: 'Cotton', villages: 16, areaHa: 739, sowing: 'Onset of monsoon to 15 July' },
  { id: 'lamjana', name: 'Lamjana', district: 'Latur', taluka: 'Ausa', crop: 'Soybean', modelCrop: 'Soyabean', villages: 13, areaHa: 9527, sowing: '15 June to 15 July' },
  { id: 'renapur', name: 'Renapur', district: 'Latur', taluka: 'Renapur', crop: 'Soybean', modelCrop: 'Soyabean', villages: 9, areaHa: 8992, sowing: '15 June to 15 July' },
  { id: 'mategaon', name: 'Mategaon', district: 'Beed', taluka: 'Georai', crop: 'Cotton', modelCrop: 'Cotton', villages: 9, areaHa: 2393, sowing: 'Onset of monsoon to 15 July' },
  { id: 'umapur', name: 'Umapur', district: 'Beed', taluka: 'Georai', crop: 'Cotton', modelCrop: 'Cotton', villages: 9, areaHa: 2048, sowing: 'Onset of monsoon to 15 July' },
  { id: 'hoal', name: 'Hoal', district: 'Beed', taluka: 'Kaij', crop: 'Soybean', modelCrop: 'Soyabean', villages: 10, areaHa: 8171, sowing: '15 June to 15 July' },
  { id: 'wida', name: 'Wida', district: 'Beed', taluka: 'Kaij', crop: 'Soybean', modelCrop: 'Soyabean', villages: 11, areaHa: 7846, sowing: '15 June to 15 July' },
];

export const CLUSTER_PAIRS: ClusterPair[] = [
  { id: 'latur-cotton', district: 'Latur', crop: 'Cotton', modelCrop: 'Cotton', sowing: 'Onset of monsoon to 15 July', rcIds: ['khandali', 'hadolati'] },
  { id: 'latur-soybean', district: 'Latur', crop: 'Soybean', modelCrop: 'Soyabean', sowing: '15 June to 15 July', rcIds: ['lamjana', 'renapur'] },
  { id: 'beed-cotton', district: 'Beed', crop: 'Cotton', modelCrop: 'Cotton', sowing: 'Onset of monsoon to 15 July', rcIds: ['mategaon', 'umapur'] },
  { id: 'beed-soybean', district: 'Beed', crop: 'Soybean', modelCrop: 'Soyabean', sowing: '15 June to 15 July', rcIds: ['hoal', 'wida'] },
];

export function circleById(id: string): RevenueCircle | undefined {
  return REVENUE_CIRCLES.find((rc) => rc.id === id);
}

export function pairById(id: string): ClusterPair | undefined {
  return CLUSTER_PAIRS.find((p) => p.id === id);
}
