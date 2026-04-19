import ClaimFields from './ClaimFields.jsx'
import CoverageResult from './CoverageResult.jsx'
import ActionResult from './ActionResult.jsx'
import SmsPreview from './SmsPreview.jsx'
import GateBanner from './GateBanner.jsx'

export default function Dashboard({
  sessionId, mode, gates, extractedFields, customerRecord, intakeComplete,
  coverage, action, sms,
  onFieldEdit, onApproveCoverage, onApproveAction, onApproveSms,
  onRetryCoverage, onRetryAction, onRetrySms,
}) {
  return (
    <>
      <GateBanner gates={gates} />
      <ClaimFields
        fields={extractedFields}
        customerRecord={customerRecord}
        mode={mode}
        editable={intakeComplete}
        onFieldEdit={onFieldEdit}
      />
      <CoverageResult
        state={coverage.state}
        data={coverage.data}
        mode={mode}
        onApprove={onApproveCoverage}
        onRetry={onRetryCoverage}
      />
      <ActionResult
        state={action.state}
        data={action.data}
        extractedFields={extractedFields}
        mode={mode}
        onApprove={onApproveAction}
        onRetry={onRetryAction}
      />
      <SmsPreview
        state={sms.state}
        data={sms.data}
        mode={mode}
        onApprove={onApproveSms}
        onRetry={onRetrySms}
      />
    </>
  )
}
