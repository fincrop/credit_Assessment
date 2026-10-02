/**
 * Monitoring jobs live in `monitoring_jobs`, separate from classification
 * and from credit-assessment jobs.
 */

import { connectToDatabase } from '../../../lib/mongodb';
import {
  TARGET_DB,
  ownedBy,
  ownedByFilter,
  pipelineBaseUrl,
  pipelineHeaders,
  requireUser,
} from '../../classification/lib/jobs';

export {
  ownedBy,
  ownedByFilter,
  pipelineBaseUrl,
  pipelineHeaders,
  requireUser,
};

export async function jobsCollection() {
  const { client } = await connectToDatabase();
  return client.db(TARGET_DB).collection('monitoring_jobs');
}
