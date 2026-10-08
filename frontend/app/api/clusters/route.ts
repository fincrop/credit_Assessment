import { NextRequest, NextResponse } from 'next/server';
import { jobsCollection, ownedByFilter, requireUser } from '../classification/lib/jobs';
import { jobsCollection as monitoringJobs } from '../monitoring/lib/jobs';
import { circleById, pairById } from '../../clusters/catalog';

interface ClassStat {
  crop?: string;
  area_ha?: number;
  mean_confidence?: number;
}

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function earlier(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a < b ? a : b;
}

function later(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a > b ? a : b;
}

export async function GET(req: NextRequest) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;

    const pairId = req.nextUrl.searchParams.get('pair') || 'latur-cotton';
    const pair = pairById(pairId);
    if (!pair) {
      return NextResponse.json({ error: 'Unknown cluster pair' }, { status: 400 });
    }

    const circles = pair.rcIds.map((id) => circleById(id)).filter((rc) => rc != null);
    const classCol = await jobsCollection();
    const monCol = await monitoringJobs();
    const classDocs = await classCol
      .find(
        { ...ownedByFilter(auth.user), 'inputs.rc_id': { $in: pair.rcIds } },
        {
          projection: {
            stage: 1,
            inputs: 1,
            total_area_ha: 1,
            'result.aoi_name': 1,
            'result.stats': 1,
            'result.mean_confidence': 1,
            'result.classified_area_ha': 1,
            'result.field_count': 1,
            'result.total_area_ha': 1,
          },
        },
      )
      .toArray();

    const completeIds = classDocs
      .filter((doc) => doc.stage === 'complete')
      .map((doc) => String(doc._id));
    const monDocs = completeIds.length
      ? await monCol
          .find(
            {
              ...ownedByFilter(auth.user),
              'inputs.classification_job_id': { $in: completeIds },
            },
            {
              projection: {
                stage: 1,
                inputs: 1,
                'result.cluster_summary': 1,
              },
            },
          )
          .toArray()
      : [];

    const rcs = circles.map((rc) => {
      const mine = classDocs.filter((doc) => {
        const inputs = (doc.inputs || {}) as { rc_id?: string };
        return inputs.rc_id === rc.id;
      });
      const done = mine.filter((doc) => doc.stage === 'complete');
      const doneIds = new Set(done.map((doc) => String(doc._id)));
      let declaredArea = 0;
      let othersArea = 0;
      let classifiedArea = 0;
      let confNum = 0;
      let confDen = 0;
      let fields = 0;
      const villages: { name: string; job_id: string; area_ha: number }[] = [];

      for (const doc of done) {
        const result = (doc.result || {}) as {
          aoi_name?: string;
          stats?: ClassStat[];
          mean_confidence?: number;
          classified_area_ha?: number;
          field_count?: number;
          total_area_ha?: number;
        };
        const inputs = (doc.inputs || {}) as { region_name?: string };
        const area = num(result.total_area_ha) || num(doc.total_area_ha);
        const stats = Array.isArray(result.stats) ? result.stats : [];
        for (const stat of stats) {
          const ha = num(stat.area_ha);
          if (stat.crop === rc.modelCrop) declaredArea += ha;
          if (stat.crop === 'Others') othersArea += ha;
        }
        classifiedArea += num(result.classified_area_ha) || area;
        fields += num(result.field_count);
        const conf = num(result.mean_confidence);
        if (conf > 0 && area > 0) {
          confNum += conf * area;
          confDen += area;
        }
        villages.push({
          job_id: String(doc._id),
          name: inputs.region_name || result.aoi_name || 'Village',
          area_ha: Math.round(area * 10) / 10,
        });
      }

      const monitors = monDocs.filter((doc) => {
        const inputs = (doc.inputs || {}) as { classification_job_id?: string };
        return doc.stage === 'complete' && doneIds.has(String(inputs.classification_job_id || ''));
      });
      const stress: Record<string, number> = {};
      let sowEarly: string | null = null;
      let sowLate: string | null = null;
      let yieldSum = 0;
      let yieldN = 0;
      for (const doc of monitors) {
        const summary = ((doc.result || {}) as { cluster_summary?: Record<string, unknown> }).cluster_summary || {};
        const counts = summary.stress_counts;
        if (counts && typeof counts === 'object') {
          for (const [k, v] of Object.entries(counts as Record<string, unknown>)) {
            stress[k] = (stress[k] || 0) + num(v);
          }
        }
        sowEarly = earlier(sowEarly, typeof summary.sowing_earliest === 'string' ? summary.sowing_earliest : null);
        sowLate = later(sowLate, typeof summary.sowing_latest === 'string' ? summary.sowing_latest : null);
        if (typeof summary.mean_yield_t_ha === 'number') {
          yieldSum += summary.mean_yield_t_ha;
          yieldN += 1;
        }
      }

      return {
        ...rc,
        classifiedVillages: done.length,
        running: mine.filter((doc) => doc.stage !== 'complete' && doc.stage !== 'failed').length,
        failed: mine.filter((doc) => doc.stage === 'failed').length,
        uploadedAreaHa: Math.round(villages.reduce((s, v) => s + v.area_ha, 0) * 10) / 10,
        declaredCropAreaHa: Math.round(declaredArea * 10) / 10,
        othersAreaHa: Math.round(othersArea * 10) / 10,
        declaredShare: classifiedArea > 0 ? Math.round((declaredArea / classifiedArea) * 1000) / 1000 : null,
        meanConfidence: confDen > 0 ? Math.round((confNum / confDen) * 1000) / 1000 : null,
        fieldCount: fields,
        villageRuns: villages,
        monitoredVillages: monitors.length,
        stress,
        sowingEarliest: sowEarly,
        sowingLatest: sowLate,
        meanYieldTHa: yieldN > 0 ? Math.round((yieldSum / yieldN) * 100) / 100 : null,
      };
    });

    return NextResponse.json({ pair, rcs });
  } catch (e) {
    console.error('[clusters]', e);
    return NextResponse.json({ error: 'Could not load the cluster comparison' }, { status: 500 });
  }
}
