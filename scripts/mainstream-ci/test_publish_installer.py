"""Synthetic unit fixtures for fail-closed publication; not native receipts."""
import copy
import unittest
import publish_installer as p


def fixture():
    migration: dict[str, object] = {flag:True for flag in ('homebrewInstall','legacyCaskPinned','bootstrapUninstallPreservedApp',
                                      'originalUserDataBytesPreserved','oldAppRetained','realCanonicalClone','updaterEntrypointPresent')}
    migration.update(archiveSha256='a'*64, publicArchiveUrl=p.PUBLIC_URL)
    evidence = {'arch':'arm64','watcherExitCode':0,'trackedChangesAfterBuild':'','fullMigration':migration,
                'officialUpdateCycle':{'status':'advanced','automaticRelaunchObserved':True,'remoteRoutePreserved':True,
                                       'before':'b'*40,'after':'c'*40,'stamp':{'commit':'c'*40}},
                'migratorStaging':{'signaturePreserved':True},
                'nativeStorageAudit':[{'seed':s,'detected':True,'originalBytesPreserved':True,'refused':s=='local'} for s in ('remote','local')],
                'nativeStartup':{'remoteRoutePersisted':True}}
    watch = {'observedUntilStop':True,'observerErrors':[],'violations':[],'samples':20}
    return evidence, watch


class PublicationTests(unittest.TestCase):
    def test_only_exact_successful_main_workflow_can_publish(self):
        run = {'repository':{'full_name':p.REPO},'head_repository':{'full_name':p.REPO},'head_branch':'main',
               'head_sha':'d'*40,'event':'workflow_dispatch','conclusion':'success','status':'completed',
               'path':'.github/workflows/mainstream-client.yml'}
        p.validate_run(run, 'd'*40)
        for key,value in [('head_branch','feature'),('conclusion','failure'),('head_sha','e'*40),('event','pull_request')]:
            bad=copy.deepcopy(run); bad[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): p.validate_run(bad, 'd'*40)

    def test_each_missing_native_gate_blocks_publication(self):
        evidence, watch=fixture()
        p.validate_evidence(evidence, watch, 'a'*64)
        for key in evidence['fullMigration']:
            bad=copy.deepcopy(evidence); del bad['fullMigration'][key]
            with self.subTest(gate=key), self.assertRaises(ValueError): p.validate_evidence(bad,watch,'a'*64)
        for key,value in [('observedUntilStop',False),('observerErrors',['error']),('violations',[{'pid':1}]),('samples',0)]:
            bad=copy.deepcopy(watch); bad[key]=value
            with self.subTest(gate=key), self.assertRaises(ValueError): p.validate_evidence(evidence,bad,'a'*64)
        for key in ('officialUpdateCycle','migratorStaging','nativeStorageAudit','nativeStartup'):
            bad=copy.deepcopy(evidence); del bad[key]
            with self.subTest(gate=key), self.assertRaises(ValueError): p.validate_evidence(bad,watch,'a'*64)


if __name__ == '__main__': unittest.main()
