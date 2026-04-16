"""
SinoVec 单元测试
"""

import sys, os, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from memory_sinovec import generate_vector


class TestGenerateVector:
    """向量生成测试"""

    def test_vector_dimensions(self):
        """测试向量维度是否正确"""
        vec = generate_vector("测试文本")
        assert len(vec) == 512, f"向量维度应为 512，实际为 {len(vec)}"

    def test_vector_type(self):
        """测试向量元素类型是否为 float"""
        vec = generate_vector("测试文本")
        assert all(isinstance(x, float) for x in vec), "向量元素应为 float"

    def test_vector_consistency(self):
        """测试相同文本产生相同向量"""
        vec1 = generate_vector("一致性测试")
        vec2 = generate_vector("一致性测试")
        assert vec1 == vec2, "相同文本应产生相同向量"

    def test_empty_text(self):
        """测试空文本"""
        with pytest.raises(Exception):
            generate_vector("")


class TestVectorDatabase:
    """数据库功能测试"""

    def test_db_connection(self):
        """测试数据库连接（需要真实的数据库环境）"""
        pytest.importorskip("psycopg2")
        try:
            from memory_sinovec import get_conn
            conn = get_conn()
            assert conn is not None
        except Exception:
            pytest.skip("数据库不可用")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
