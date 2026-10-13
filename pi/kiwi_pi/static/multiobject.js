(function(root) {
  function boxStyle(box, size) {
    const [width, height] = size;
    return {left: `${box[0]/width*100}%`, top: `${box[1]/height*100}%`,
      width: `${(box[2]-box[0])/width*100}%`, height: `${(box[3]-box[1])/height*100}%`};
  }
  function guideProgress(result, status) {
    const guide = status.guide || {};
    if (!result.operation_id || guide.operation_id !== result.operation_id) return null;
    const total = new Set((result.slots || []).map(slot => slot.id)).size;
    return {total, confirmed: (guide.confirmed || []).length, pending: guide.pending || [],
      complete: guide.phase === 'returned', phase: guide.phase,
      wrong_slots: guide.wrong_slots || [], blocked: guide.blocked || []};
  }
  const api = {boxStyle, guideProgress};
  if (typeof module !== 'undefined') module.exports = api;
  else root.KiwiMultiobject = api;
})(typeof window !== 'undefined' ? window : {});
