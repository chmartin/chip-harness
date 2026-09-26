import { MongoClient } from "mongodb";

export const dynamic = "force-dynamic";
let clientPromise;
function client() {
  if (!clientPromise) {
    clientPromise = new MongoClient(process.env.MONGODB_URI, { serverSelectionTimeoutMS: 8000 }).connect();
    clientPromise.catch(() => { clientPromise = null; }); // don't cache a failed connection
  }
  return clientPromise;
}

export async function GET() {
  try {
    const db = (await client()).db(process.env.MONGODB_DB || "chipharness");
    const [versions, trials] = await Promise.all([
      db.collection("harness_versions").find({}, { projection: { plateau_policy: 0, llm: 0 } })
        .sort({ version: 1 }).toArray(),
      db.collection("trials").find({ status: { $in: ["done", "error"] }, design: { $ne: null } }, {
        projection: { harness_version: 1, clock_target_mhz: 1, iteration: 1, created_at: 1, finished_at: 1, status: 1, goal: 1,
          score: 1, "stages.tier2.wns_ns": 1, "stages.tier2.drc_count": 1, "stages.tier2.area_um2": 1,
          "stages.testbench.status": 1, "diagnosis.fix_family": 1 },
      }).sort({ finished_at: 1 }).limit(1000).toArray(),
    ]);
    return Response.json({ versions, trials, at: new Date().toISOString() });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 500 });
  }
}
