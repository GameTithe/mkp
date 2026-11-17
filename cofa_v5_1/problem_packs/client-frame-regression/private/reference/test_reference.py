import unittest
from decimal import Decimal as D
from framescope import p95, analyze, normalize

class ReferenceTests(unittest.TestCase):
    def test_rank20(self): self.assertEqual(p95(list(map(D, range(1,21)))), D(19))
    def test_rank21(self): self.assertEqual(p95(list(map(D, range(1,22)))), D(20))
    def test_empty_rank(self): self.assertIsNone(p95([]))
    def test_empty_document(self): self.assertEqual(analyze({'schema_version':1,'frames':[]})['items'], [])
    def test_bool_not_integer(self): self.assertIn('frame', normalize(dict(build='baseline',scenario='x',frame=True,cpu_ms=1,gpu_ms=1))[1])
    def test_nan_not_numeric(self): self.assertIn('cpu_ms', normalize(dict(build='baseline',scenario='x',frame=3,cpu_ms=float('nan'),gpu_ms=1))[1])

if __name__ == '__main__': unittest.main()
